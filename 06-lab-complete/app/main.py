"""
Production AI Agent — Part 6 Final Project

Chạy:   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Kiểm:   python check_production_ready.py

Luồng /ask:
  X-API-Key (401) → rate limit Redis (429) → budget Redis (402) → LLM → ghi cost
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "event": record.getMessage(),
            "pid": record.process,
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)  # chỉ vào log, không trả cho client
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers[:] = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False  # không để root logger in thêm bản text
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields):
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
#
# Khi nhận SIGTERM (docker stop, platform redeploy), uvicorn:
#   1. ngừng nhận kết nối mới
#   2. chờ các request đang chạy trả lời xong
#   3. chạy phần sau `yield` bên dưới rồi thoát với exit code 0
# Vì vậy KHÔNG tự đăng ký signal handler (dễ chặn mất handler của uvicorn).
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              redis_ready=redis_healthy(), rate_limit_per_minute=settings.rate_limit_per_minute)
    yield
    log_event("shutdown", detail="in-flight requests finished, closing Redis connections")
    try:
        r.close()
    except Exception:
        pass


app = FastAPI(
    title="Production AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Gắn request_id, đo latency, ghi 1 dòng log JSON cho mỗi request.
    Lỗi không lường trước → 500 JSON gọn, stack trace chỉ nằm trong log."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_error", extra={"fields": {
            "request_id": request_id, "path": request.url.path}})
        response = JSONResponse(status_code=500, content={
            "detail": "Internal server error", "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    log_event("request", request_id=request_id, method=request.method, path=request.url.path,
              status=response.status_code, ms=round((time.perf_counter() - start) * 1000, 1))
    return response


# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"app": "Production AI Agent", "environment": settings.environment,
            "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống → 200. KHÔNG phụ thuộc Redis (Redis chết mà
    restart container thì cũng không chữa được, chỉ làm restart loop)."""
    return {"status": "ok", "environment": settings.environment,
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready")
def ready():
    """Readiness: Redis không kết nối được → 503 để load balancer ngừng gửi traffic."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Not ready: Redis unavailable")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)          # 429
    check_budget()                     # 402

    answer = llm_ask(body.question)

    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", user_id=body.user_id, question_chars=len(body.question),
              input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8))

    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }

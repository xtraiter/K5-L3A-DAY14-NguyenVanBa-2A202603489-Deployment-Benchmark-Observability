"""
Rate limiting (tiêu chí 7) — sliding window 60 giây bằng Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis: mọi worker / instance dùng chung, restart agent vẫn còn
"""
import hashlib
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    # Không lưu API key dạng rõ trong Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:24]


def check_rate_limit(key: str) -> None:
    bucket = _bucket(key)
    limit = settings.rate_limit_per_minute
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"  # unique, kể cả 2 request cùng thời điểm

    try:
        # MULTI/EXEC: 4 lệnh chạy liền một khối, các worker khác không chen vào giữa
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)  # bỏ request ngoài cửa sổ
        pipe.zadd(bucket, {member: now})                         # ghi request hiện tại
        pipe.zcard(bucket)                                       # đếm trong cửa sổ
        pipe.expire(bucket, WINDOW_SECONDS + 1)                  # key tự dọn khi không dùng
        _, _, count, _ = pipe.execute()

        if count <= limit:
            return

        # Bị chặn: xoá lần thử này để spam không kéo dài thời gian bị khoá
        r.zrem(bucket, member)
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Rate limiter storage unavailable, please retry")

    raise HTTPException(
        status_code=429,
        detail=f"Rate limit exceeded: max {limit} requests per {WINDOW_SECONDS}s",
        headers={
            "Retry-After": str(max(1, retry_after)),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )

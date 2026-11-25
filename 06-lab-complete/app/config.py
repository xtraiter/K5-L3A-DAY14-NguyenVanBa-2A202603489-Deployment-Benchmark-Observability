"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Không có secret nào nằm trong code. Giá trị mặc định chỉ để chạy local;
trên cloud mọi biến được đặt trong dashboard của nền tảng (Railway / Render).
"""
import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Server
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Security — để trống thì mọi request /ask đều bị 401 (an toàn mặc định)
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # State dùng chung giữa các worker / instance
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Rate limit & cost guard
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))

    def validate(self) -> "Settings":
        """Fail fast: production mà quên đặt API key thì không cho khởi động."""
        if self.environment == "production" and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY must be set when ENVIRONMENT=production")
        return self


settings = Settings().validate()

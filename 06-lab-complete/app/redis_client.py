"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • redis.from_url() KHÔNG kết nối ngay lúc import → app vẫn khởi động được khi Redis chưa sẵn sàng
  • Timeout ngắn để /ready trả 503 nhanh khi Redis chết, thay vì treo request
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: ping được Redis → True, lỗi kết nối / timeout → False."""
    try:
        return bool(r.ping())
    except (redis.RedisError, OSError):
        return False

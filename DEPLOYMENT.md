# Deployment Information

## Public URL

https://agent-production-6435bc21.up.railway.app

## Platform

**Railway**: project `k5-day14-agent`, gồm 2 service:

| Service | Nguồn | Ghi chú |
|---|---|---|
| `agent` | Thư mục `06-lab-complete/`, build bằng `Dockerfile` (theo `railway.toml`) | Healthcheck `/health`, restart khi crash |
| `Redis` | Railway Redis (`railway add -d redis`) | Agent kết nối qua mạng nội bộ `redis.railway.internal:6379` |

## Test Commands

Đặt biến trước khi test. API key **không** ghi trong repo, xem bằng `railway variables -s agent --kv` trong thư mục `06-lab-complete/`.

```bash
BASE=https://agent-production-6435bc21.up.railway.app
AGENT_API_KEY=<key đặt trên Railway>
```

### Tự kiểm CP6 (mỗi lệnh chỉ in mã HTTP)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-25: `200`, `200`, `401`, `200`.

### Health check

```bash
curl "$BASE/health"
# {"status":"ok","environment":"production","timestamp":"..."}
```

### API test (có API key)

```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","user_id":"test","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}
```

### Rate limiting (10 request / 60 giây cho mỗi API key)

```bash
for i in $(seq 1 12); do
  curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"test"}'
done; echo
# Đã chạy sau 2 request trước đó: 200 200 200 200 200 200 200 200 429 429 429 429
```

### Script chấm

```bash
cd 06-lab-complete
python check_production_ready.py --public-url https://agent-production-6435bc21.up.railway.app
# ✅ Public URL: GET https://agent-production-6435bc21.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), không ghi trong code hay trong repo:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `PORT` | Railway tự cấp (8080) | `CMD` trong Dockerfile đọc `${PORT:-8000}` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Tham chiếu tới service Redis, Railway tự điền URL nội bộ |
| `AGENT_API_KEY` | chuỗi ngẫu nhiên 43 ký tự (`secrets.token_urlsafe(32)`) | Secret, chỉ nằm trên Railway |
| `ENVIRONMENT` | `production` | Ở production app không khởi động nếu thiếu `AGENT_API_KEY`; `/docs` bị tắt |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | Vượt → 402 |
| `MONTHLY_BUDGET_USD` | `10.0` | Vượt → 402 |
| `LOG_LEVEL` | `INFO` | |

## Cách deploy

```bash
npm i -g @railway/cli
railway login
cd 06-lab-complete
railway init -n k5-day14-agent
railway add -d redis                       # service Redis
railway add -s agent                       # service agent (trống)
railway variables -s agent --skip-deploys \
  --set 'ENVIRONMENT=production' --set 'REDIS_URL=${{Redis.REDIS_URL}}' \
  --set 'RATE_LIMIT_PER_MINUTE=10' --set 'DAILY_BUDGET_USD=1.0' \
  --set 'MONTHLY_BUDGET_USD=10.0' --set 'LOG_LEVEL=INFO'
python -c "import secrets; print(secrets.token_urlsafe(32), end='')" \
  | railway variables -s agent --skip-deploys --set-from-stdin AGENT_API_KEY
railway up -s agent --ci                   # upload thư mục, build Dockerfile, deploy
railway domain -s agent                    # sinh URL public
```

Log khởi động trên Railway xác nhận app đọc đúng cấu hình và kết nối được Redis:

```
event="startup" environment="production" port=8080 redis_ready=true rate_limit_per_minute=10
```

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)

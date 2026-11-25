# Day 14 Lab — Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

1. **Hardcode secret** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL` chứa mật khẩu `password123`. Push lên GitHub là lộ key ngay, và xoá file sau đó cũng không đủ vì key vẫn nằm trong lịch sử git.
2. **Ghi secret ra log** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Khi chạy thử, log in ra đúng dòng `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`. Ai đọc được log là có key.
3. **`print()` thay cho logging** (dòng 33, 34, 38): không có level, không có timestamp, không phải JSON, nên log aggregator không lọc hay tìm được. Còn in nguyên câu hỏi và câu trả lời của user ra log.
4. **Config cứng trong code** (dòng 21–22): `DEBUG = True`, `MAX_TOKENS = 500`. Muốn đổi giữa dev/staging/prod phải sửa code và build lại.
5. **Không có health check**: `GET /health` trả **404** khi chạy thử, nên platform không biết lúc nào app chết để restart.
6. **`host="localhost"`** (dòng 51): chỉ nhận kết nối từ chính máy đó. Chạy trong container thì từ ngoài không gọi vào được, phải dùng `0.0.0.0`.
7. **Port cứng `8000`** (dòng 52): Railway và Render cấp port qua biến `PORT`. Khi deploy Part 6 lên Railway, port được cấp là 8080, không phải 8000.
8. **`reload=True`** (dòng 53): chế độ dev, theo dõi file và tự restart. Ở production nó tốn tài nguyên và có thể restart giữa chừng.
9. **Không xử lý shutdown**: không có lifespan, không xử lý SIGTERM, nên dễ tắt ngang request đang chạy.
10. **Nhận `question` qua query string** (dòng 31): câu hỏi của user nằm trong URL nên bị ghi vào access log (`"POST /ask?question=Hello HTTP/1.1"`). Gửi JSON thì bị 422, và không giới hạn độ dài câu hỏi.

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}   [422]

$ curl http://localhost:8000/health
{"detail":"Not Found"}   [404]
```

Log của server khi gọi `/ask`:

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [30260] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
INFO:     127.0.0.1:60930 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

App chạy được, nhưng chưa sẵn sàng cho production: lộ secret trong log, không có health check, chỉ nghe trên `localhost`.

### Exercise 1.3: Comparison table

Bản production chạy với `PORT=8001 ENVIRONMENT=staging`:

```text
$ curl -i http://localhost:8001/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":1.0,"version":"1.0.0","environment":"staging","timestamp":"2026-11-25T10:31:34.521792+00:00"}

$ curl -i http://localhost:8001/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -X POST http://localhost:8001/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}   [200]

$ ENVIRONMENT=production python -c "import config"      # không đặt AGENT_API_KEY
ValueError: AGENT_API_KEY must be set in production!
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hằng số trong code (`DEBUG = True`, `MAX_TOKENS = 500`) | `config.py`: mọi giá trị đọc từ env, có `.env.example` | Cùng một code/image chạy ở dev, staging, prod, chỉ đổi biến môi trường. Không phải sửa code để đổi cấu hình |
| Secrets | `OPENAI_API_KEY = "sk-..."` trong code và còn in ra log | `os.getenv("OPENAI_API_KEY")`; thiếu `AGENT_API_KEY` khi `ENVIRONMENT=production` thì dừng ngay (`ValueError`, đã thử) | Secret không vào git và không vào log. Fail fast ngay lúc khởi động tốt hơn chạy production mà không có bảo vệ |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env (đã chạy ở 8001) | Container và cloud cần nghe trên `0.0.0.0` và đúng port nền tảng cấp |
| Health check | Không có (`/health` → 404) | `/health` (liveness: uptime, version, env) và `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform dựa vào liveness để restart app chết, load balancer dựa vào readiness để không gửi traffic vào instance chưa sẵn sàng |
| Logging | `print()`, in cả secret | Ý định là log JSON (`logging` + `json.dumps`), không log secret, chỉ log độ dài câu hỏi | Log có cấu trúc thì lọc và tìm được trong Loki/Datadog. Không log dữ liệu nhạy cảm của user |
| Shutdown | Đột ngột | Nhận SIGTERM thì uvicorn ngừng nhận request mới, chờ request đang chạy xong, rồi chạy phần shutdown của `lifespan` (đặt `is_ready = False`, ghi log, dọn dẹp) | Không cắt ngang request của user khi deploy bản mới hoặc scale xuống |
| Debug / reload | `reload=True` luôn bật | `reload` chỉ bật khi `DEBUG=true` | Production không cần theo dõi file, tránh restart ngoài ý muốn |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho các frontend được phép gọi API từ trình duyệt |

**Nhận xét khi chạy thật:** bản production **không in ra dòng log JSON nào** (không có `startup`, `agent_request`), chỉ có log của uvicorn và `WARNING:root:OPENAI_API_KEY not set`. Tôi kiểm tra thì sau khi import, root logger có level `WARNING` và format `%(levelname)s:%(name)s:%(message)s`. Nguyên nhân: `config.py` gọi `logging.warning(...)` trong `validate()` **trước** khi `app.py` gọi `logging.basicConfig(format=JSON)`. Lệnh `logging.warning` đầu tiên tự tạo handler mặc định, nên `basicConfig` sau đó không còn tác dụng. Bài học: cấu hình logging phải chạy trước mọi lệnh log. Part 6 tránh lỗi này bằng cách gắn handler JSON riêng cho logger `agent`.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` (dòng 8), bản đầy đủ gồm Debian, Python và build tools. Vì vậy image develop rất nặng (1.67 GB, xem 2.3).
2. **Working directory:** `/app` (dòng 11). Mọi lệnh `COPY` và `CMD` sau đó chạy tương đối với `/app`.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer, và một layer chỉ build lại khi input của nó hoặc layer trước nó thay đổi. Copy `requirements.txt` (dòng 14) rồi `pip install` (dòng 17) **trước** khi copy code (dòng 20) thì sửa `app.py` chỉ làm build lại từ dòng 20. Layer cài dependencies (bước chậm nhất) được dùng lại từ cache. Nếu `COPY . .` trước thì mỗi lần sửa code đều phải cài lại toàn bộ thư viện.
4. **CMD vs ENTRYPOINT:**
   - `CMD` là lệnh mặc định khi container chạy; `docker run my-agent:develop <lệnh khác>` sẽ **ghi đè** nó.
   - `ENTRYPOINT` cố định chương trình chạy; tham số của `docker run` được **nối thêm** vào sau, chỉ đổi được bằng `--entrypoint`.
   - Cách thường gặp: `ENTRYPOINT` là executable, `CMD` là tham số mặc định.
   - Ở đây dùng `CMD ["python", "app.py"]` dạng exec (JSON array) nên `python` là PID 1 và nhận được SIGTERM khi `docker stop`. Dạng shell `CMD python app.py` thì PID 1 là `/bin/sh`.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .      # 113 s
$ docker run -d -p 8000:8000 my-agent:develop

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.8,"container":true}

$ docker exec <container> sh -c 'whoami; python --version; ls /app'
root
Python 3.11.18
__pycache__  app.py  requirements.txt  utils
```

Container develop chạy bằng **root**. Nếu app bị khai thác thì kẻ tấn công có quyền root trong container. Bản production sửa điểm này bằng `USER appuser`.

### Exercise 2.3: Multi-stage build và image size

- **Stage 1 (`builder`, dòng 16–29):** từ `python:3.11-slim`, cài `gcc` và `libpq-dev` để build các package cần biên dịch, rồi `pip install --user` vào `/root/.local`. Stage này chỉ dùng để build, không được deploy.
- **Stage 2 (`runtime`, dòng 36–72):**
  - bắt đầu lại từ `python:3.11-slim` sạch, tạo user `appuser`;
  - chỉ `COPY --from=builder /root/.local` (các package đã cài) cùng `main.py` và `utils/`;
  - `USER appuser`, có `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.
- **Vì sao nhỏ hơn:**
  - base `slim` thay cho bản full (riêng `python:3.11-slim` là 189 MB);
  - `gcc`, header và cache `apt` nằm lại ở stage builder, image cuối không có;
  - chỉ copy kết quả cài đặt, không copy công cụ build.

  Image nhỏ hơn thì pull và deploy nhanh hơn, và bề mặt tấn công nhỏ hơn.

```text
$ docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .     # 98 s
$ docker images my-agent
REPOSITORY   TAG        IMAGE ID       SIZE
my-agent     advanced   909463c1ff5a   240MB
my-agent     develop    e840882ff2a9   1.67GB
```

| Image | `docker images` (trên đĩa) | `docker image inspect .Size` (nén) |
|---|---:|---:|
| Develop (`python:3.11`, 1 stage) | 1.67 GB | 426,320,140 B ≈ 406.6 MiB |
| Production (`python:3.11-slim`, multi-stage) | 240 MB | 57,409,172 B ≈ 54.7 MiB |
| **Giảm** | **≈ 85.6 %** (1 − 240/1670) | **≈ 86.5 %** |

Docker Desktop đang dùng containerd image store, nên hai cột khác nhau: `docker images` báo dung lượng giải nén trên đĩa, còn `inspect` báo dung lượng nén. Cả hai đều cho thấy multi-stage + slim giảm khoảng 86%.

### Exercise 2.4: Docker Compose stack

```text
Client
  │  :80 / :443  (cổng duy nhất mở ra ngoài)
  ▼
┌────────────────────┐
│ nginx (nginx:alpine)│  reverse proxy + rate limit 10 r/s (burst 20) + security headers
└─────────┬──────────┘
          │ proxy_pass http://agent:8000   (Docker DNS)
          ▼
┌──────────────────────────────┐
│ agent (build multi-stage)    │  FastAPI, 2 uvicorn workers, HEALTHCHECK /health
└───────┬──────────────┬───────┘
        │ REDIS_URL     │ QDRANT_URL
        ▼               ▼
┌──────────────┐  ┌────────────────┐
│ redis:7-alpine│  │ qdrant v1.9.0  │   volume redis_data / qdrant_data
└──────────────┘  └────────────────┘
        tất cả nằm trong bridge network "internal"
```

**Các service được start:** 4 service (`agent`, `redis`, `qdrant`, `nginx`).

```text
$ docker compose -p k5-day14-docker up -d --build --wait
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 5 seconds (healthy)    8000/tcp
nginx     nginx:alpine            Up 5 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 16 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 16 seconds (healthy)   6379/tcp
```

**Chúng giao tiếp thế nào:**
- Cả 4 service nằm chung network `internal` và gọi nhau bằng **tên service** qua DNS của Docker (`agent:8000`, `redis:6379`, `qdrant:6333`).
- `depends_on` với `condition: service_healthy` làm agent chỉ start sau khi Redis và Qdrant đã healthy, và Nginx start sau agent.
- Chỉ Nginx publish port. Gọi thẳng `localhost:8000` từ máy host thì không kết nối được (`000`), mọi request phải đi qua Nginx.
- Agent nhận `REDIS_URL` và `QDRANT_URL` qua biến môi trường, nhưng `main.py` của ví dụ này chưa thật sự gọi Redis hay Qdrant; hai service này là phần dựng sẵn cho bài sau.

**Test qua Nginx:**

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":4.4,"version":"2.0.0","timestamp":"2026-11-25T10:37:58.014514"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

**Quan sát thêm:**
- **Bind mount trên Google Drive bị lỗi.** Lần đầu chạy, Nginx không start được: `error mounting ".../nginx/nginx.conf" ... not a directory`. Docker Desktop không bind-mount được file nằm trên ổ Google Drive (G:). Tôi chạy lại từ một bản copy của repo trên ổ C: (`git archive HEAD`) thì chạy bình thường.
- **Log của agent không phải JSON hợp lệ.** Một dòng log trông như `{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 21}"}`. Lý do: format string của `logging` bọc nguyên chuỗi JSON vào `"%(message)s"` mà không escape dấu `"`. Muốn log là JSON thật thì phải dựng cả object rồi mới `json.dumps`, như `JsonFormatter` ở Part 6.

---

## Part 3: Cloud Deployment

Theo lịch K5, Part 3 deploy chính agent Part 6 (`06-lab-complete/`), không deploy app mẫu trong `03-cloud-deployment/`. Chi tiết đầy đủ: [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.1: Railway deployment (nền tảng chính)

- **URL:** https://agent-production-6435bc21.up.railway.app
- **Service:** `agent` (build từ `06-lab-complete/Dockerfile` theo `railway.toml`) + `Redis` (Railway Redis). Agent đọc `REDIS_URL=${{Redis.REDIS_URL}}`, đi qua mạng nội bộ `redis.railway.internal`.
- **Biến môi trường** đặt trên Railway, không nằm trong code: `AGENT_API_KEY` (sinh ngẫu nhiên), `REDIS_URL`, `ENVIRONMENT=production`, `RATE_LIMIT_PER_MINUTE=10`, `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`, `LOG_LEVEL`. `PORT` do Railway cấp (8080).
- **4 lệnh tự kiểm CP6** (chạy ngày 2026-11-25):

  | Lệnh | Mong đợi | Thực tế |
  |---|---|---|
  | `GET /health` | 200 | 200 |
  | `GET /ready` | 200 | 200 |
  | `POST /ask` không có `X-API-Key` | 401 | 401 |
  | `POST /ask` có `X-API-Key` đúng | 200 | 200 |

  Thêm: gửi liên tiếp 12 request thì từ request thứ 11 trong cửa sổ 60 giây bắt đầu nhận `429` (`RATE_LIMIT_PER_MINUTE=10`).
- **Screenshot:** [dashboard](screenshots/dashboard.png) · [service running](screenshots/running.png) · [test](screenshots/test.png)

### Exercise 3.2: So sánh `railway.toml` với `render.yaml`

So sánh hai file cấu hình trong `06-lab-complete/` (cùng deploy một agent):

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Cấu hình **một service**: cách build và cách chạy | **Blueprint cho cả hạ tầng**: khai báo nhiều service (agent + Redis) trong một file | Railway coi project là tập service tạo bằng CLI/dashboard, file chỉ mô tả từng service. Render theo hướng Infrastructure as Code: đọc file là dựng lại được toàn bộ |
| Định dạng | TOML, chia `[build]` / `[deploy]` | YAML, danh sách `services:` | Quy ước riêng của từng nền tảng |
| Cách build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir`, `dockerfilePath`, `dockerContext` | Render đọc Blueprint ở gốc repo GitHub nên phải chỉ rõ thư mục con `06-lab-complete`. Railway `railway up` upload thẳng thư mục hiện tại |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 60` | `healthCheckPath: /health` | Cùng ý tưởng, khác tên khoá. Railway cho chỉnh timeout chờ deploy healthy |
| Restart | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Không khai báo (Render tự restart) | Railway cho chọn chính sách restart trong file |
| Biến môi trường và secret | **Không có trong file**. Đặt bằng `railway variables` / dashboard | **Khai báo trong file**. Giá trị thường ghi thẳng (`ENVIRONMENT: production`), secret dùng `generateValue: true` (Render tự sinh `AGENT_API_KEY`) | File Render nằm trong git nên secret không được ghi giá trị, chỉ ghi cách tạo (`generateValue`) hoặc `sync: false` (nhập tay trên dashboard) |
| Redis | Thêm bằng lệnh `railway add -d redis`, nối bằng biến tham chiếu `${{Redis.REDIS_URL}}` | Khai báo service `type: keyvalue` ngay trong file, nối bằng `fromService: … property: connectionString`, `ipAllowList: []` để chỉ cho kết nối nội bộ | Hệ quả của khác biệt "một service" và "cả hạ tầng" ở dòng đầu |
| Region, gói | Chọn trên dashboard | `region: singapore`, `plan: free` trong file | Render đưa cả lựa chọn hạ tầng vào IaC |
| Cách kích hoạt deploy | `railway up` từ máy (hoặc nối GitHub) | Push lên GitHub → Render tự deploy (`autoDeploy: true`) | Railway thiên về CLI, Render thiên về GitOps |
| `PORT` | Cả hai không khai báo: nền tảng tự cấp `PORT`, và `CMD` trong Dockerfile đọc `${PORT:-8000}` | | Một image chạy được trên cả hai nền tảng (12-factor) |

So với file mẫu trong `03-cloud-deployment/`: bản mẫu Railway dùng `builder = "NIXPACKS"` + `startCommand` có `$PORT`, bản mẫu Render dùng `runtime: python` + `buildCommand`/`startCommand`. Bản của Part 6 dùng Dockerfile cho cả hai, nên môi trường chạy trên cloud giống hệt image đã test bằng `check_production_ready.py`.

### Exercise 3.3: Pipeline CI/CD trên Cloud Run (đọc `cloudbuild.yaml` + `service.yaml`)

1. Push lên `main` kích hoạt Cloud Build. Bước **test** chạy `pytest tests/` trong `python:3.11-slim`. Các bước sau đều `waitFor` bước trước, nên test fail là pipeline dừng, không build, không deploy.
2. Bước **build** tạo Docker image gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache. Bước **push** đẩy cả hai tag lên Container Registry `gcr.io/$PROJECT_ID/ai-agent`.
3. Bước **deploy** chạy `gcloud run deploy` với image `:$COMMIT_SHA` (biết chính xác commit nào đang chạy, rollback được). Các tham số: region `asia-southeast1`, 1–10 instance (`min-instances=1` để tránh cold start), 512Mi/1 CPU, timeout 60s, `OPENAI_API_KEY` lấy từ Secret Manager thay vì ghi trong file.
4. `service.yaml` mô tả cùng service theo kiểu khai báo (Knative):
   - autoscaling 1–10 instance, `containerConcurrency: 80` request/instance;
   - giới hạn tài nguyên;
   - secret qua `secretKeyRef`;
   - `livenessProbe` gọi `/health` (fail thì restart), `startupProbe` gọi `/ready` (chưa sẵn sàng thì chưa nhận traffic).
5. Nhận xét: thư mục mẫu không có `tests/`, `requirements.txt` hay `Dockerfile`, nên muốn chạy thật phải thêm vào. Bước test cũng nên chạy `run_eval.py` làm quality gate như Part 7.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được kiểm tra ở đâu:** trong dependency `verify_api_key()`. Nó đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` và được gắn vào `/ask` bằng `Depends(verify_api_key)`. Endpoint nào cần bảo vệ thì chỉ cần thêm dependency đó.
- **Sai key thì sao:** thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key`. (Part 6 yêu cầu cả hai trường hợp đều trả 401.)
- **Rotate key thế nào:** key đọc từ biến `AGENT_API_KEY` lúc import (dòng 35), nên rotate bằng cách đặt giá trị mới trên nền tảng rồi restart hoặc redeploy. Code hiện chỉ nhận một key, nên lúc đổi thì client cũ bị từ chối ngay. Muốn không gián đoạn thì cho phép **2 key cùng lúc** (key cũ + key mới) trong thời gian chuyển, rồi gỡ key cũ.
- **Vấn đề khác thấy trong code:**
  - Thiếu env thì dùng giá trị mặc định `"demo-key-change-in-production"`.
  - Lúc khởi động in nguyên key ra console (dòng 91–92).

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**Luồng JWT (`auth.py`):**
1. `POST /auth/token` với username/password → `authenticate_user()` kiểm tra `DEMO_USERS`.
2. `create_token()` ký payload `{sub, role, iat, exp = +60 phút}` bằng HS256 với `JWT_SECRET`.
3. Client gửi `Authorization: Bearer <token>`. `verify_token()` dùng `jwt.decode` để kiểm chữ ký và hạn dùng, rồi trả `{username, role}`.

Server không cần tra database ở mỗi request vì mọi thông tin nằm trong token có chữ ký. Ngược lại, muốn thu hồi token trước khi hết hạn thì khó.

Kết quả chạy (token đã che):

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
content-type: application/json

{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

$ curl -X POST http://localhost:8000/auth/token -d '{"username": "student", "password": "wrong"}'
{"detail":"Invalid credentials"}   [401]

$ curl -X POST http://localhost:8000/ask -d '{"question": "Explain JWT"}'                 # không có token
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}   [401]

$ curl -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" -d '{"question": "Explain JWT"}'
{"detail":"Invalid token."}   [403]

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

**Nhận xét:**
- `JWT_SECRET` có giá trị mặc định hardcode (`auth.py` dòng 22). Nếu quên đặt env thì ai biết chuỗi này đều tự ký được token admin.
- Mật khẩu demo lưu dạng plaintext (dòng 28–29); thực tế phải hash (bcrypt/argon2).

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Algorithm:** **sliding window log**. Mỗi user có một `deque` lưu timestamp các request. Mỗi lần gọi, bỏ các timestamp cũ hơn 60 giây; nếu còn ≥ limit thì trả 429, không thì ghi thêm timestamp hiện tại.
- **Limit:** user thường **10 request/phút** (`rate_limiter_user`), admin **100 request/phút** (`rate_limiter_admin`).
- **Bypass cho admin:** `/ask` chọn limiter theo `role` trong JWT. Token của `teacher` (role `admin`) dùng limiter 100/phút. Đã thử: 15 request liên tiếp bằng token `teacher` đều 200.
- **Hạn chế:** state nằm trong RAM của process. Restart là mất, và chạy nhiều worker hay instance thì mỗi nơi đếm riêng (thực tế limit thành 10 × số process). Vì vậy Part 6 chuyển sang sorted set trong **Redis**.

Vòng lặp 20 request bằng token `student`. Trước đó đã dùng 1 request ở 4.2, nên request thứ 10 trong vòng lặp là request thứ 11 trong 60 giây:

```text
Request  1: 200 OK
Request  2: 200 OK
...
Request  9: 200 OK
Request 10: 429 Too Many  Retry-After=56  Remaining=0
Request 11: 429 Too Many  Retry-After=56  Remaining=0
...
Request 20: 429 Too Many  Retry-After=54  Remaining=0

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -d '{"question": "Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795603193
retry-after: 53
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":53}}
```

### Exercise 4.4: Cost guard implementation

**Rate limit và cost guard bảo vệ hai thứ khác nhau:**
- Rate limit bảo vệ **tốc độ** (số request mỗi phút, chống spam và quá tải).
- Cost guard bảo vệ **tiền** (tổng token đã tiêu).

Một user gửi 10 câu hỏi rất dài mỗi phút vẫn nằm trong rate limit nhưng vẫn có thể đốt hết budget. Ngược lại, nhiều request nhỏ có thể rẻ nhưng làm quá tải hệ thống. Vì vậy cần cả hai.

**Bản mẫu `04-api-gateway/production/cost_guard.py`:**
- Đếm token theo user và theo ngày **trong RAM**: budget $1/ngày/user (vượt → **402**), budget tổng $10/ngày (vượt → **503**), cảnh báo trong log khi dùng 80%.
- Kết quả thật: `/me/usage` sau 10 request trả `{"requests":10,"input_tokens":40,"output_tokens":300,"cost_usd":0.000186,"budget_usd":1.0,"budget_remaining_usd":0.999814}`.
- Lỗi nhỏ: field `budget_remaining_usd` trong response `/ask` (`app.py` dòng 160) thực ra trả về `usage.total_cost_usd`, tức **số đã dùng**, không phải số còn lại.

**Cách tôi làm (Part 6, `06-lab-complete/app/cost_guard.py`, lưu trong Redis):**
- `check_budget()` được gọi **trước** khi gọi LLM. Nó đọc `cost:day:<YYYY-MM-DD>` và `cost:month:<YYYY-MM>` (theo UTC); nếu đã chạm `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` thì trả **402** kèm số đã tiêu và thời điểm reset.
- `record_cost()` được gọi **sau** khi LLM trả lời. Nó tính `input/1000 × $0.00015 + output/1000 × $0.0006`, rồi `INCRBYFLOAT` vào cả hai key trong một transaction, kèm `EXPIRE` (2 ngày và 32 ngày) để key tự hết hạn khi sang ngày/tháng mới.
- State nằm trong Redis nên mọi worker và instance dùng chung, restart không mất. `check_production_ready.py` đặt budget 0.000001 USD thì nhận 402.

Đáp án cho đúng hàm của đề (budget $10/tháng/user):

```python
import os
from datetime import datetime, timezone

import redis

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def check_budget(user_id: str, estimated_cost: float) -> bool:
    key = f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"   # key mới mỗi tháng → tự reset
    new_total = r.incrbyfloat(key, estimated_cost)                  # cộng nguyên tử: "giữ chỗ" trước
    r.expire(key, 32 * 24 * 3600)
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)                         # vượt → trả lại phần vừa giữ
        return False
    return True
```

Khác với lời giải mẫu (`GET` rồi mới `INCRBYFLOAT`), bản này cộng trước rồi kiểm tra sau. Hai worker chạy cùng lúc không thể cùng đọc thấy "còn budget" rồi cùng vượt, vì `INCRBYFLOAT` là thao tác nguyên tử.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- **`/health` (liveness):** trả 200 cùng uptime, version, environment và kiểm tra RAM bằng `psutil` (dùng ≥ 90% thì `status: degraded`). Chỉ trả lời câu hỏi "process còn sống không"; fail thì platform restart container.
- **`/ready` (readiness):** trả **503** khi `_is_ready = False` (đang khởi động hoặc đang shutdown), 200 khi sẵn sàng. Fail thì load balancer ngừng gửi traffic vào instance đó nhưng **không** restart nó.
- Hai endpoint phải tách nhau. Nếu `/health` phụ thuộc Redis thì Redis chết sẽ làm mọi container bị restart liên tục mà không chữa được gì. Lỗi phụ thuộc nên làm `/ready` fail thôi (Part 6 làm đúng như vậy).

```text
$ curl http://127.0.0.1:8000/health
200 {"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T10:39:26.796682+00:00","checks":{"memory":{"status":"ok","used_percent":6.6}}}
$ curl http://127.0.0.1:8000/ready
200 {"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Chạy trong WSL2 (Linux, gửi được SIGTERM thật). Script test gửi `POST /ask?question=Long task`, 50 ms sau thì `kill -TERM <pid>`:

```text
$ kill -TERM 2396   (50 ms sau khi gửi request)
Request đang chạy -> 200 {"answer":"Đây là câu trả lời từ AI agent (mock). ..."}  (3278 ms)
Process exit code -> 0

--- server log ---
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Shutting down
INFO:     127.0.0.1:55928 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 17:39:29,912 INFO 🔄 Graceful shutdown initiated...
2026-11-25 17:39:29,912 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [2396]
2026-11-25 17:39:29,912 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Request có hoàn thành không:** có. Request đang chạy vẫn nhận **200**, process thoát với **exit code 0**. Trình tự trong log:
1. uvicorn nhận SIGTERM → `Shutting down`, ngừng nhận kết nối mới;
2. chờ request `/ask` xong (200);
3. chạy phần shutdown của `lifespan`;
4. thoát.

Handler `handle_sigterm` tự đăng ký chỉ in log **sau cùng**: uvicorn tạm chiếm tín hiệu trong lúc chạy, tắt xong mới trả tín hiệu lại cho handler cũ. Đó là lý do khi tự viết handler phải gọi lại handler của uvicorn, nếu không process sẽ không tắt (Part 6 dùng `lifespan` và không tự đăng ký handler).

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- **Anti-pattern:** `conversation_history = {}` trong RAM. Mỗi instance có RAM riêng: request 1 vào instance A lưu history ở A, request 2 vào instance B thì B không thấy gì. Restart hay deploy lại thì mất hết.
- **Bản production:** mọi state nằm trong Redis.
  - `save_session()` dùng `SETEX session:<id>` với TTL 3600 giây.
  - `load_session()` dùng `GET`.
  - `append_to_history()` giữ tối đa 20 tin nhắn.

  Instance nào cũng đọc ghi được session của mọi user.
- **Điểm cần chú ý:**
  - Nếu lúc import không ping được Redis thì code **tự lùi về dict trong RAM** (`USE_REDIS = False`). App vẫn chạy nhưng âm thầm trở thành stateful.
  - Ở production nên fail hẳn (để `/ready` trả 503) thay vì lùi về RAM. Part 6 làm theo hướng này: Redis lỗi thì trả 503, không có chế độ lưu trong RAM.

### Exercise 5.4: Load balancing

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 6 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 17 seconds (healthy)   6379/tcp
```

10 request `POST http://localhost:8080/chat`. Header `X-Served-By` do Nginx thêm, `served_by` do agent trả về:

```text
Request  1 -> upstream 172.18.0.5:8000    served_by=instance-4b0141
Request  2 -> upstream 172.18.0.4:8000    served_by=instance-33e62e
Request  3 -> upstream 172.18.0.3:8000    served_by=instance-85441e
Request  4 -> upstream 172.18.0.5:8000    served_by=instance-4b0141
Request  5 -> upstream 172.18.0.4:8000    served_by=instance-33e62e
Request  6 -> upstream 172.18.0.3:8000    served_by=instance-85441e
Request  7 -> upstream 172.18.0.5:8000    served_by=instance-4b0141
Request  8 -> upstream 172.18.0.4:8000    served_by=instance-33e62e
Request  9 -> upstream 172.18.0.3:8000    served_by=instance-85441e
Request 10 -> upstream 172.18.0.5:8000    served_by=instance-4b0141

$ docker compose -p k5-day14-scaling logs agent | grep "POST /chat"    (đếm theo container)
      3 agent-1
      4 agent-2
      3 agent-3
```

Nginx chia **round-robin** đều cho 3 instance. Lúc Nginx khởi động, tên `agent` trong `upstream` được DNS của Docker phân giải ra IP của cả 3 container, nên `upstream` có 3 server. `proxy_next_upstream error timeout http_503` giúp request tự chuyển sang instance khác khi một instance lỗi (xem 5.5).

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 31d4e2e0-e4a0-4ccf-be8a-059607fac4e8

Request 1: [instance-33e62e]   Q: What is Docker?
Request 2: [instance-85441e]   Q: Why do we need containers?
Request 3: [instance-4b0141]   Q: What is Kubernetes?
Request 4: [instance-33e62e]   Q: How does load balancing work?
Request 5: [instance-85441e]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-4b0141', 'instance-33e62e', 'instance-85441e'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

5 câu hỏi trong cùng một session được 3 instance khác nhau xử lý, nhưng history vẫn đủ 10 tin nhắn (5 user + 5 assistant), vì instance nào cũng ghi vào cùng key `session:<id>` trong Redis.

Script không tự tắt instance nào, nên tôi làm thêm phần "kill instance" như mô tả trong `CODE_LAB.md`. Tôi dừng hẳn `agent-1` (`instance-33e62e`), rồi gửi tiếp 4 câu vào **cùng session**:

```text
$ docker stop k5-day14-scaling-agent-1
HTTP 200 served_by=instance-85441e turn=7  storage=redis
HTTP 200 served_by=instance-85441e turn=8  storage=redis
HTTP 200 served_by=instance-4b0141 turn=9  storage=redis
HTTP 200 served_by=instance-85441e turn=10 storage=redis
$ curl http://localhost:8080/chat/<session>/history
messages = 18      # 9 câu user: 5 câu cũ + 4 câu sau khi dừng agent-1
```

Kết quả:
- Mất một instance thì Nginx chuyển traffic sang 2 instance còn lại; cả 4 request đều 200.
- Conversation vẫn liền mạch vì state không nằm trong instance đã chết.

Lỗi nhỏ: `turn` bị lệch 1 (câu thứ 6 báo `turn=7`). Lý do: `app.py` dòng 154 đếm số câu user **sau khi** đã thêm câu hiện tại vào history, rồi lại `+ 1`.

---

## Part 6: Final Project

Code trong `06-lab-complete/`. Chạy `python check_production_ready.py` đầy đủ trong WSL2 Ubuntu (`os.name = posix`, Python 3.11, Docker 29.7.2):

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số con số trong output: image 63 MB; `docker stop` thoát exit code 0 sau 0.9s; rate limit trả 429 sau 5 request (`RATE_LIMIT_PER_MINUTE=5`) và vẫn 429 sau khi restart agent (state trong Redis).

---

## AI log

> TODO: xem lại, điền cột "Tôi tự sửa / kiểm tra thế nào" và bổ sung các lần dùng AI cho Part 7.

| # | Phần | Hỏi AI gì | AI trả về gì | Tôi tự sửa / kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Toàn bài | Đọc toàn bộ tài liệu lab, tóm tắt yêu cầu và cách chấm | Tóm tắt thứ tự CP0–CP8, rubric, các điểm script chấm hay bắt lỗi | |
| 2 | Part 6 | Viết code cho các TODO trong `06-lab-complete/` (config, auth, rate limiter Redis, cost guard Redis, `/health`, `/ready`, log JSON, Dockerfile multi-stage, compose, `railway.toml`, `render.yaml`) | Code hoàn chỉnh cho 12 tiêu chí | Chạy `check_production_ready.py` trong WSL2 → `PASS 12/12`; thử tay bằng `docker compose up` (401/422/429) |
| 3 | Part 6 | Vì sao chạy script trên Windows không PASS, cách bật Docker trong WSL | Phải chạy trong WSL (SIGTERM); bật WSL integration của Docker Desktop, thêm user vào nhóm `docker` | Tự bật trong Docker Desktop, chạy lại script |
| 4 | Part 3 | Deploy agent Part 6 lên Railway kèm Redis, đặt biến môi trường | Các lệnh `railway init/add/variables/up/domain` | Chạy 4 lệnh CP6 trên URL public: 200/200/401/200 |
| 5 | Part 3 | Viết `DEPLOYMENT.md`, so sánh `railway.toml`/`render.yaml`, mô tả pipeline Cloud Run | Bản nháp các mục này | |
| 6 | Part 1, 2, 4, 5 | Chạy các ví dụ develop/production, lấy output `curl -i`, kích thước image, kết quả SIGTERM, load balancing, test stateless; viết câu trả lời | Output thật của các lệnh + bản nháp câu trả lời và các lỗi phát hiện được (log JSON bị nuốt ở Part 1, JSON log không hợp lệ ở Part 2, `budget_remaining_usd` sai ở Part 4, `turn` lệch 1 ở Part 5) | |
| 7 | Part 2, 5 | Vì sao Nginx trong Compose không start (`error mounting nginx.conf`) | Docker Desktop không bind-mount được file trên Google Drive (G:); chạy Compose từ bản copy trên ổ C: | |

# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ file report JSON/CSV đã nộp (đường dẫn ghi ở từng mục), không ước lượng.

**Họ tên:** Nguyen Van Ba — 2A202603489
**Ngày chạy:** 2026-11-25
**Môi trường:**
- Laptop AMD Ryzen 7 4800H (8 nhân / 16 luồng), 32 GB RAM, Windows 11.
- Bước 1 chạy trong **WSL2 Ubuntu** (kernel 6.18, Python 3.11.18). Agent (1 process uvicorn) và `load_test.py` chạy chung một máy.
- Bước 2: Docker Desktop 29.7.2 (WSL2 backend).

Bước 1 chạy trong WSL2 thay vì Windows vì trên Windows `asyncio.sleep` chỉ chính xác tới khoảng 15 ms, sẽ làm lệch phép đo latency.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất khoảng 150–250 ms mỗi lần gọi (p95 khoảng 220 ms khi không quá tải, bảng 2.1). 500 ms chừa khoảng 2 lần để chịu jitter và tải tăng, mà người dùng chat vẫn thấy phản hồi gần như tức thì. Không đặt theo p99 vì mock cố tình có khoảng 3% request chậm 1,5 s; với SLO p95 thì phần đuôi đó nằm trong error budget, còn alert dùng ngưỡng p95 > 1 s |
| Error rate `/ask` | < 2 % | Upstream LLM lỗi ngẫu nhiên khoảng 1% (`MOCK_ERROR_RATE=0.01`), đo được 0,33–1,33% (bảng 2.1). 2% vẫn chừa error budget gấp đôi mức lỗi nền. Alert đặt ở 5% trong 1 phút để không báo động vì spike ngắn |
| Accuracy trên golden dataset | ≥ 85 % và **không regression** | Bằng ngưỡng của job CI (mục 3). Cấm regression vì một câu từng đúng mà giờ sai là dấu hiệu bản mới làm hỏng thứ đang chạy, kể cả khi accuracy tổng không giảm. Bản hiện tại đạt 100% |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`. Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.13 | 188.3 | 221.0 | 1709.9 | 1.33 % (4 × 503) | ✅ PASS |
| 10 | 36.73 | 194.5 | 220.9 | 1671.1 | 1.33 % (4 × 503) | ✅ PASS |
| 50 | 104.97 | 203.7 | 1680.1 | 1721.1 | 1.00 % (3 × 503) | ❌ FAIL (p95) |
| 100 | 51.91 | 456.5 | 3932.8 | 4300.4 | 0.33 % (1 × 503) | ❌ FAIL (p95) |

**Nhận xét: RPS có tăng tuyến tính theo concurrency không? Vì sao?**

- **Từ c=1 đến c=10: gần tuyến tính** (4.13 → 36.73, ×8.9). Mỗi request chủ yếu *chờ* LLM (`asyncio.sleep` trong mock), không tốn CPU. Event loop xếp chồng được nhiều request đang chờ, nên latency gần như không đổi (p50 188 → 195 ms). Theo định luật Little, `RPS ≈ concurrency / latency`: 1 / 0.244 s ≈ 4.1 và 10 / 0.232 s ≈ 43 req/s, khớp với số đo.
- **Từ c=10 đến c=50: bắt đầu dưới tuyến tính** (×2.9 thay vì ×5). Theo Little lẽ ra đạt 50 / 0.292 s ≈ 171 req/s, nhưng chỉ đo được 105. Phần CPU của mỗi request (FastAPI, middleware, log JSON, phía client `httpx`) bắt đầu đáng kể trên một process duy nhất.
- **Ở c=50, p95 = 1680 ms không phải do quá tải** mà do tail latency: khoảng 3% request chậm thêm 1,5 s (`MOCK_SLOW_RATE=0.03`). Với 300 mẫu, p95 nằm sát ranh giới nhóm chậm, nên lúc rơi vào nhóm chậm, lúc không. Ở các lần chạy lặp lại cùng cấu hình (không lưu file), p95 ở c=50 lúc 275 ms, lúc 286 ms, lúc 302 ms. p50 vẫn chỉ 204 ms.
- **Ở c=100: hệ thống bão hoà, RPS còn giảm** (105 → 52) và p50 tăng gấp đôi, p95 khoảng 3,9 s.
  - Tôi đo thêm latency **phía server** (field `ms` trong log JSON của agent), xem `reports/extra_serverside_vs_client_latency.txt`. Ở c=100, phía server p50 = 199.9 ms, p95 = 231.1 ms, tức là gần như không đổi. Cùng lúc đó client đo được p95 = 3844.6 ms.
  - Vậy thời gian thêm vào **không nằm trong handler của agent** mà nằm ở khâu xếp hàng trước handler: mở 100 kết nối cùng lúc, event loop của 1 process uvicorn, và chính client `httpx` chạy chung máy với agent.
  - Kernel không ghi nhận SYN bị rớt (`ListenOverflows = 0`), nên không phải do hàng đợi TCP đầy.
  - Lúc chạy phép đo trong file này, máy đang bận việc khác (VS Code, Discord...), nên cả lần c=50 trong file cũng cho client p95 = 3731.8 ms, cao hơn nhiều so với `load_v2_c50.json`. Phía server của lần đó vẫn chỉ p95 = 464.4 ms. Kết luận giữ nguyên: khi tải cao, phần lớn latency client thấy nằm ngoài agent, và đo trên một máy dùng chung thì nhiễu lớn. Vì vậy cần đo cả hai phía và lặp lại nhiều lần.
- **Hướng mở rộng:** tăng số worker hoặc instance (Bước 2, mục 5), và đặt máy tạo tải tách khỏi agent khi đo chính thức.

### 2.2 Tail latency

v2 được bật lại với từng giá trị `MOCK_SLOW_RATE`, cùng lệnh `--requests 300 --concurrency 20`. Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 190.4 | 219.9 | 226.4 |
| 0.03 (mặc định) | 227.1 | 225.5 | 1680.7 |
| 0.1 | 369.0 | 1695.5 | 1718.3 |

(Cùng các file đó: p50 lần lượt 190.5 / 191.8 / 195.3 ms; RPS 102.83 / 69.73 / 43.99.)

**Nhận xét: chỉ số nào nhạy nhất với request chậm? Vì sao SLO không nên đặt theo average?**

- **Ở 3% request chậm, p99 nhạy nhất:** 226 → 1681 ms (×7.4), trong khi avg chỉ tăng 37 ms (+19%), còn p50 và p95 gần như không đổi. Nhìn avg 227 ms thì thấy "vẫn ổn", nhưng thực tế cứ 100 người dùng có khoảng 3 người chờ gần 1,7 s.
- **Ở 10% request chậm** (vượt 5%), cả **p95** cũng nhảy lên 1696 ms và vi phạm SLO 500 ms, còn avg mới là 369 ms. Nếu đặt SLO theo avg < 500 ms thì vẫn "PASS", trong khi 1/10 request chậm hơn 1,6 s.
- **RPS cũng giảm** (103 → 70 → 44 req/s): concurrency cố định 20, request chậm giữ chỗ lâu hơn, nên số request hoàn thành mỗi giây giảm (định luật Little). Tail latency làm hại cả throughput, không chỉ trải nghiệm người dùng.
- **Kết luận:** average là trung bình cộng, nên một nhóm nhỏ request rất chậm bị "pha loãng" bởi số đông request nhanh. SLO phải đặt theo percentile (p95/p99), vì percentile trả lời đúng câu hỏi "bao nhiêu % người dùng được phục vụ đủ nhanh".

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (baseline = v1), `reports/eval_v2_fixed.json` (baseline = v2).

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100.0 % (23/23) |
| Avg tokens / câu | 56.7 | 109.0 | 117.0 |
| Tổng cost (USD) | 0.000398 | 0.000759 | 0.000819 |
| Cost / câu đúng (USD) | 0.00039795 | 0.00003616 | 0.00003563 |
| Latency p95 trong eval (ms) | 187.8 | 209.0 | 214.6 |
| Quality gate | FAIL (≥ 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

**Những câu v2 trả lời sai ban đầu và cách sửa:**

Cả hai câu đều sai vì retrieval không tìm thấy tài liệu nào (knowledge base chưa có nội dung về HPA và JWT). Agent rơi về câu trả lời chung chung *"Câu hỏi hay! Điều này phụ thuộc vào kiến trúc hệ thống..."*, score 0.0.

| ID | Keyword bị thiếu | Tôi đã sửa gì |
|---|---|---|
| `scaling-03` (*Kubernetes HPA scale dựa trên những chỉ số nào?*) | `cpu`, `memory`, `custom metrics` | Thêm vào `KNOWLEDGE_BASE` (`develop/utils/mock_llm.py`) một tài liệu với từ khoá kích hoạt `["hpa", "autoscal", "kubernetes"]`: HPA tăng/giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) so với ngưỡng mục tiêu |
| `security-02` (*JWT khác gì API key trong xác thực?*) | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu với từ khoá `["jwt", "json web token"]`: API key là chuỗi cố định, không tự hết hạn; JWT là token có chữ ký số, chứa user và thời điểm hết hạn, server kiểm chữ ký mà không cần tra database |

- **Cách tránh regression:** chọn từ khoá kích hoạt chỉ khớp đúng 2 câu này trong `golden_dataset.jsonl` (không câu nào khác chứa `hpa`, `kubernetes`, `jwt`). Nhờ vậy 21 câu đang đúng vẫn lấy đúng tài liệu cũ.
- **Kết quả:** eval cuối với `--min-accuracy 1.0 --baseline reports/eval_v2.json` cho 23/23, **"Câu được cải thiện: 2, Câu bị regression: 0"**, `Quality gate ... ✅ PASS`, exit code 0.
- Cùng thay đổi được chép sang `production/utils/mock_llm.py` để stack Docker ở Bước 2 cũng đạt 100%. Không sửa `golden_dataset.jsonl`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chi phí mỗi câu:** v2 dùng **gần gấp đôi token** (56.7 → 109.0, +92%), vì prompt kèm thêm tài liệu được retrieve. Tổng cost vì thế **×1.9** (0.000398 → 0.000759 USD).
- **Latency:** v2 chậm hơn một chút: p95 +21 ms, gồm bước retrieval (khoảng 20 ms) và prompt dài hơn (mock cộng 0,2 ms mỗi token input).
- **Chất lượng:** accuracy tăng **×21** (4.3% → 91.3%). Vì thế **cost cho mỗi câu trả lời đúng giảm khoảng 11 lần** (0.000398 → 0.0000362 USD). v1 rẻ mỗi câu nhưng gần như trả lời sai hết, nên tiền bỏ ra gần như vô ích. "Cost / câu" khiến v1 trông rẻ hơn, còn "cost / câu đúng" cho thấy v2 mới là bản đáng tiền.
- **Sau khi sửa KB:** thêm 8 token mỗi câu (+7%), tổng cost +0.000060 USD, để có thêm 2 câu đúng. Cost / câu đúng còn giảm nhẹ (0.0000362 → 0.0000356). Đổi như vậy là đáng.

**Quality gate trong CI (Exercise 7.3):** job ở `.github/workflows/quality-gate.yml`.
- Chạy khi mở pull request đụng vào `develop/`. Job bật agent v2 với `MOCK_ERROR_RATE=0` (để lỗi ngẫu nhiên không làm gate fail oan), rồi chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`.
- `run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có câu bị regression. Step fail kéo theo job fail; bật branch protection *Require status checks* thì PR không merge được.
- Đã thử: chạy eval cho **v1** với `--baseline reports/eval_v2.json` thì báo `Câu bị regression: 20`, `Quality gate (accuracy ≥ 80%, không regression): ❌ FAIL`, **exit code 1**. Đây chính là trường hợp gate chặn một bản làm chất lượng giảm mạnh.

---

## 4. Observability stack (production)

**Cách chạy:**
- `docker compose -p k5-day14-observability up -d --build`, chạy từ một bản copy của repo trên ổ C:, vì Docker Desktop không bind-mount được file trên Google Drive.
- `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.
- Phải sửa `production/Dockerfile`: thêm `ENV PYTHONUSERBASE=/home/agent/.local`. Thiếu dòng này thì agent crash ngay (`ModuleNotFoundError: No module named 'uvicorn'`), vì user `agent` có home là `/app` nên Python tìm package sai chỗ.

### 4.1 Screenshot

Ảnh được chụp bằng Chromium headless (Playwright) nối vào network của stack, sau khi đã dựng đúng trạng thái cần chụp (xem AI log).

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): `screenshots/grafana-dashboard.png`. Locust 30 user, cấu hình mặc định, 10 phút gần nhất, chụp lúc khoảng 12:38 UTC.
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): `screenshots/jaeger-slow-trace.png` (mục 4.3)
- [x] Một trace lỗi trong Jaeger (có event `exception`): `screenshots/jaeger-error-trace.png` (mục 4.3)
- [x] Trang Alerts của Prometheus với ít nhất 2 alert đang **firing**: `screenshots/prometheus-alert-firing.png`. Bật cùng lúc A (`MOCK_ERROR_RATE=0.2`) và C (`LLM_MODEL=mock-llm-large`): `AgentHighErrorRate` FIRING (value 0.133) và `LLMCostBurnRateHigh` FIRING (value 7.80), còn `AgentHighLatencyP95` và `AgentInstanceDown` inactive.

### 4.0 Exercise 7.4: đọc dashboard khi có tải

Locust 30 user (ramp 5), 2 instance, cấu hình mặc định. Số liệu Prometheus (cửa sổ 1 phút) lưu ở `production/benchmark-results/ex74_prometheus_30users.txt`; ảnh dashboard chụp cùng lúc.

1. **p95 và avg là bao nhiêu? Khoảng cách nói lên điều gì?**
   - p95 = **0.293 s** (panel Latency p95: 290.29 ms), avg = **0.231 s**, p99 = **1.822 s**.
   - avg và p95 chỉ cách nhau khoảng 60 ms: 95% request có thời gian gần nhau (khoảng 150–300 ms, chính là latency của LLM).
   - Khoảng cách thật nằm giữa p95 và p99 (gấp khoảng 6 lần), cũng là đường p99 tách hẳn lên khoảng 1.8 s trên panel *Latency /ask*. Đó là khoảng 3% request rơi vào LLM chậm. Avg (0.23 s) không cho thấy nhóm này.
2. **Nginx có chia đều traffic cho 2 instance không?** Có. Panel *Requests/s theo instance* có 2 đường gần như trùng nhau: **9.24 và 8.91 req/s** (khoảng 51% / 49%), đúng kiểu round-robin.
3. **Vì sao input tokens/s lớn hơn output tokens/s?** Input **1428 tokens/s** so với output **762 tokens/s** (khoảng 1.9 lần). Prompt gửi LLM gồm system prompt + **tài liệu retrieve được (RAG)** + câu hỏi, còn output chỉ là câu trả lời. Agent v2 càng nhét nhiều context thì input càng lớn. Dù input nhiều token hơn, **output vẫn chiếm phần lớn chi phí**, vì giá output đắt gấp 4 lần ($0.0006 so với $0.00015 mỗi 1K token): 762 × 0.0006 so với 1428 × 0.00015, tức khoảng 68% chi phí đến từ output.
4. **`curl http://localhost:8080/metrics` trả về gì? Vì sao?** Trả **404**. `nginx.conf` có `location = /metrics { return 404; }`:
   - `/metrics` lộ thông tin nội bộ (route, lưu lượng, phiên bản, model, chi phí), không nên public ra Internet.
   - Prometheus không cần đi qua Nginx: nó scrape **trực tiếp** từng container `agent:8000/metrics` trong mạng Docker nội bộ (`dns_sd_configs`). Nếu scrape qua load balancer thì mỗi lần chỉ trúng một instance ngẫu nhiên và số liệu sẽ nhảy lung tung.

Cũng trong ảnh, panel **LLM cost (1h)** đỏ và `LLMCostBurnRateHigh` firing lại (cost khoảng $2.45/giờ với 30 user), đúng như nhận xét ở mục 4.2 về ngưỡng cost tuyệt đối.

### 4.2 Thí nghiệm "phá hệ thống"

**Cách chạy (tự động bằng script, để thời gian đo được chính xác):**
- Mỗi thí nghiệm: ghi `.env` → `docker compose -p k5-day14-observability up -d --no-deps agent` → hỏi `GET /api/v1/alerts` của Prometheus mỗi 5 giây, ghi lại lúc alert sang `pending` và `firing` → khôi phục `.env` → chờ alert về `inactive` rồi mới làm thí nghiệm tiếp theo.
- Riêng D dùng `docker pause` / `docker unpause` một container agent.
- Kết quả thô: `production/benchmark-results/chaos_A_B_D_E_C_122246.json`.
- Tải nền: Locust headless **15 user** (2 instance agent). Không dùng 30 user như gợi ý vì với 30 user, chi phí ở cấu hình mặc định đã khoảng $2.7/giờ, vượt ngưỡng `LLMCostBurnRateHigh` ($2/giờ). Alert cost sẽ firing sẵn từ trước và thí nghiệm C mất ý nghĩa (xem nhận xét ở cuối mục).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (critical) | pending **+37 s**, firing **+97 s** | **Error rate** (1.27% → 20.1%); p95 không đổi (0.29 s) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (warning) | pending **+27 s**, firing **+148 s** | **Latency p50/p95/p99**: p95 0.30 s → 1.91 s, trong khi avg chỉ 0.24 → 0.62 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (warning) | pending **+39 s**, firing **+160 s** | **Cost USD/giờ**: $0.92–0.93/giờ (v2 model nhỏ, đo trước B và D) → **$9.35/giờ** lúc firing (vẫn đang tăng vì cửa sổ 5 phút); p95 `/ask` 0.20 → 0.67 s (hệ số latency 2.5 của model lớn) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (critical) | pending **+10 s**, firing **+40 s** | **Instances up** 2 → 1. Panel error rate của agent vẫn **0.0** |
| E | `AGENT_VERSION=v1` | **Không có** (sau 180 s: `alerts={}`) | — | **Retrieval hit ratio** 1.0 → không còn dữ liệu (v1 không retrieve); **tokens/s** input 575 → 328, output 307 → 179; cost $0.96 → $0.69/giờ. Error rate và p95 vẫn xanh (p95 còn giảm 0.29 → 0.20 s) |

**Đọc thời gian tới lúc firing:** thời gian ≈ (lúc container mới nhận traffic) + (cửa sổ `rate()` đủ dữ liệu xấu để vượt ngưỡng) + `for:` + chu kỳ đánh giá 5 s.
- A: `for: 1m`, firing sau 97 s.
- D: `for: 30s`, nhanh nhất (40 s), vì `up == 0` là đúng ngay ở lần scrape hỏng đầu tiên.
- B và C: `for: 2m`. C còn phải chờ cửa sổ `rate(...[5m])` tích đủ chi phí, nên chậm nhất (160 s).

**Những gì metric của agent không thấy** (kiểm tra bằng log Nginx):
- **Thí nghiệm D:** Nginx trả **14 lỗi `504`** (`upstream timed out ... while reading response header`), bắt đầu lúc 12:11:28, đúng 10 giây (`proxy_read_timeout 10s`) sau khi pause. Các request đó chưa bao giờ được agent xử lý xong, nên panel error rate của agent vẫn xanh. Chỉ có 14 lỗi vì sau lần timeout đầu, Nginx tạm đánh dấu instance đó lỗi và chuyển request sang instance còn lại.
- **Mỗi lần đổi `.env` + `docker compose up -d`:** container agent bị tạo lại với IP mới. Nginx chỉ hỏi lại DNS mỗi 10 giây (`resolve`), nên trong khoảng 20 giây sau mỗi lần đổi có khoảng **100–150 lỗi `502`** (ví dụ 118 lỗi trong 12:05:1x–12:05:2x, ngay sau khi áp dụng A). Đổi cấu hình bằng cách tạo lại container như vậy **không phải zero-downtime**, và metric của agent cũng không thấy. Production cần thêm giám sát ở tầng load balancer (metric của Nginx/ingress, synthetic check từ bên ngoài) và rolling deploy có readiness check.

**Ngưỡng alert cost là con số tuyệt đối:** $2/giờ sẽ firing chỉ vì lưu lượng tăng (30 user cấu hình mặc định ≈ $2.7/giờ), dù chi phí cho mỗi request không đổi. Nên alert thêm theo **cost / request** (phát hiện đổi model hoặc prompt phình to) và theo **budget còn lại trong ngày/tháng**, thay vì chỉ một ngưỡng cố định.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm accuracy rơi từ 100% xuống khoảng 4% (mục 3), nhưng với hạ tầng thì v1 còn "khoẻ hơn": nhanh hơn, ít token hơn, không lỗi. Metric hạ tầng (4 Golden Signals) chỉ trả lời "hệ thống có chạy không", không trả lời "câu trả lời có đúng không". Cần giám sát **chất lượng**:

1. **Chặn trước khi deploy:** quality gate trong CI (`run_eval.py --baseline`, mục 3). Chính bản v1 này bị gate chặn với 20 câu regression và exit code 1.
2. **Giám sát sau deploy:**
   - Chạy golden dataset định kỳ (synthetic eval, ví dụ mỗi 15 phút) vào production, xuất accuracy thành metric `agent_eval_accuracy`, rồi alert khi giảm.
   - Alert theo metric "đại diện" cho chất lượng: retrieval hit ratio giảm đột ngột (ở thí nghiệm E nó mất hẳn), tokens/request giảm mạnh, hoặc `agent_info{version}` đổi ngoài lịch deploy.
3. **Tín hiệu từ người dùng và từ LLM:** tỉ lệ 👍/👎, tỉ lệ hỏi lại hoặc escalate. Chấm điểm một mẫu traffic thật bằng LLM-as-judge (ví dụ trong Langfuse) rồi alert khi điểm giảm.

### 4.3 Debug bằng trace

Trace chậm nhất trong 30 phút (Jaeger API `/api/v3/traces`, lọc `duration ≥ 1s`, có 455 trace thoả điều kiện; chọn trace dài nhất). Ảnh: `screenshots/jaeger-slow-trace.png`.

- `trace_id`: `3cef590bc4720f54e513a9330adbab01`
- Tổng thời gian: **1817.7 ms** (span gốc `POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`**, 1691.3 ms, **93.0%** (`gen_ai.request.model = mock-llm-small`, `gen_ai.usage.input_tokens = 76`, `gen_ai.usage.output_tokens = 39`)

  ```
  POST /ask                       1817.7 ms  (100.0%)
  └── agent.run                   1717.8 ms  ( 94.5%)
      ├── agent.retrieve_context    26.2 ms  (  1.4%)   retrieval.documents = 1
      └── chat mock-llm-small     1691.3 ms  ( 93.0%)
  ```

  Code của agent (retrieval) chỉ tốn 26 ms. Gần như toàn bộ thời gian là chờ LLM upstream, chính là một request rơi vào nhóm "slow" (`MOCK_SLOW_RATE`, +1500 ms). Muốn giảm tail latency này thì phải xử lý ở tầng gọi LLM (timeout + retry, hedged request, hoặc model/provider khác), không phải tối ưu code agent.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 3cef590bc4720f54e513a9330adbab01`):

```json
{"ts": "2026-11-25T11:59:30.086308+00:00", "level": "INFO", "service": "observable-agent", "instance": "348cd77ca165", "event": "llm_call", "trace_id": "3cef590bc4720f54e513a9330adbab01", "span_id": "28cc59bdecf7b7dd", "request_id": "a97ac355d44d", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 76, "output_tokens": 39, "cost_usd": 3.5e-05, "llm_ms": 1690.7}
{"ts": "2026-11-25T11:59:30.087247+00:00", "level": "INFO", "service": "observable-agent", "instance": "348cd77ca165", "event": "http_request", "trace_id": "3cef590bc4720f54e513a9330adbab01", "span_id": "28cc59bdecf7b7dd", "request_id": "a97ac355d44d", "method": "POST", "route": "/ask", "status": 200, "ms": 1816.5}
```

Log khớp với trace: `llm_ms = 1690.7` gần bằng span LLM 1691.3 ms, và `ms = 1816.5` gần bằng span gốc 1817.7 ms.

**Trace lỗi** (ảnh `screenshots/jaeger-error-trace.png`): `trace_id` `21d9e636de7d246edebccc1637049f5e`, tổng 102.0 ms.
- Span `chat mock-llm-small` (75.8 ms) có `status = ERROR`, `error.type = upstream_unavailable`, và event **`exception`**: `exception.type = utils.mock_llm.LLMError`, `exception.message = "Upstream LLM error: 503 Service Unavailable (mock)"`. Lỗi lan lên `agent.run` và `POST /ask` (HTTP 503).
- Lỗi đến nhanh (102 ms so với khoảng 190 ms của request thành công), vì mock trả lỗi sau một nửa latency.
- Dòng log cùng `trace_id`:

```json
{"ts": "2026-11-25T12:03:55.099325+00:00", "level": "INFO", "service": "observable-agent", "instance": "dd5dd5dd2dd9", "event": "llm_error", "trace_id": "21d9e636de7d246edebccc1637049f5e", "span_id": "bc99e77f4faca991", "request_id": "76893ce8674b", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Nhận xét: dòng `llm_error` đang ghi ở level `INFO`. Nên ghi `ERROR` (hoặc `WARNING`) để lọc log theo mức độ nghiêm trọng mà không phải biết tên event.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Nguồn: dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.82 | 200 | 310 | 1700 | 85 / 7162 (1.19 %), đều là `503` |
| 3 | 60.09 | 190 | 230 | 1700 | 71 / 7134 (1.00 %), đều là `503` |

(Dòng `Aggregated` gồm cả `/health`: RPS 65.95 và 66.09, p95 300 và 230 ms.)

**Ghi chú cách chạy:**
- Phải thêm `--no-deps`. Lệnh trong CP8 không có cờ này, và `docker compose run locust` tự bật lại các service phụ thuộc theo `replicas: 2` trong compose, nên lần chạy "1 instance" thực ra thành 2 instance. Tôi kiểm tra số instance trước và sau mỗi lần chạy: scale_1 có 1 target `agent` trong Prometheus, scale_3 có 3 target.
- Stack chạy từ một bản copy trên ổ C:, vì Docker Desktop không bind-mount được file trên Google Drive.

**Giải thích kết quả: scale có cải thiện p95 không? Nút thắt nằm ở đâu?**

- **RPS không đổi (59.8 so với 60.1)** vì Locust là tải *khép kín*: 100 user, mỗi user chờ phản hồi rồi nghỉ `between(0.5, 2)` giây (trung bình 1.25 s). Tải tối đa vì thế chỉ khoảng 100 / (1.25 + 0.24) ≈ 67 req/s, khớp với RPS `Aggregated` 66 đo được. RPS do tốc độ "người dùng" quyết định, không phải do năng lực của agent, nên thêm instance không làm RPS tăng.
- **Ngay với 1 instance, agent chưa bão hoà.** Prometheus cho thấy trong lúc chạy scale_1, agent dùng `rate(process_cpu_seconds_total) = 0.234` core trên giới hạn 0.5 core; với scale_3 mỗi instance khoảng 0.08 core. Nginx chia đều cho 3 instance: 2345 / 2302 / 2283 request `/ask` trong 2 phút.
- **p95 có cải thiện nhẹ (310 → 230 ms)**: 1 instance phục vụ khoảng 60 req/s trên 0.5 core nên có chút xếp hàng trên event loop; chia cho 3 instance thì hết.
- **p50 gần như không đổi, p99 giữ nguyên 1700 ms.** Phần đuôi do khoảng 3% lời gọi LLM chậm thêm 1.5 s, xảy ra ở *upstream*, nên thêm bao nhiêu instance cũng không rút ngắn được. Panel LLM latency cho thấy p95 LLM bằng nhau ở cả hai lần (0.198 s theo Prometheus).
- **Nút thắt là LLM upstream**, không phải agent. Với kiểu tải này, scale ngang chỉ nên làm khi CPU hoặc in-flight của agent tiến gần giới hạn. Muốn giảm p99 thì phải xử lý ở tầng LLM (timeout + retry hoặc hedged request, cache câu trả lời, model nhanh hơn). Muốn thấy lợi ích thật của scale thì phải tăng tải tới khi 1 instance bão hoà (nhiều user hơn hoặc bỏ `wait_time`).

---

## 6. Kết luận

- 3 điều tôi học được về benchmark & observability:
  1. **Phải đo đúng chỗ và đo nhiều lần.**
     - Ở c=100, client thấy p95 khoảng 4 s nhưng latency phía agent vẫn chỉ 231 ms; phần chậm nằm ngoài handler.
     - p95 ở c=50 dao động từ 275 ms tới 1680 ms giữa các lần chạy, vì p95 nằm sát nhóm 3% request chậm.
     - Lần đầu đo `MOCK_SLOW_RATE`, script của tôi vô tình gọi vào agent cũ (lưu nhầm PID), cho ra số "đẹp" nhưng sai. Chỉ phát hiện được vì kết quả mâu thuẫn với lý thuyết.

     Một con số benchmark chỉ đáng tin khi biết rõ nó được đo ở đâu, bằng cách nào và có lặp lại được không.
  2. **Average che giấu người dùng bị chậm; percentile và tail mới là thứ cần SLO.** Với 3% request chậm, avg chỉ tăng 19% nhưng p99 tăng 7 lần. Scale từ 1 lên 3 instance cũng không cứu được p99, vì nút thắt là LLM upstream chứ không phải agent.
  3. **Metric của một service chỉ thấy những gì service đó thấy.**
     - Lỗi 504 khi một instance treo và các đợt 502 mỗi lần tạo lại container chỉ hiện ở log Nginx.
     - Đổi sang agent v1 làm accuracy rơi từ 100% xuống 4% mà không alert hạ tầng nào kêu.

     Cần đủ 3 tầng: metric hạ tầng (4 Golden Signals), giám sát ở load balancer hoặc synthetic check, và eval chất lượng (CI gate + eval định kỳ). Metric, trace và log phải nối với nhau bằng `trace_id` thì mới đi từ "có vấn đề" tới "vấn đề ở bước nào".
- **Nếu đưa agent này lên production thật, tôi sẽ thêm:**
  - **Chất lượng:** `agent_eval_accuracy` từ eval định kỳ trên golden dataset, alert khi giảm quá 5 điểm %; alert khi `retrieval hit ratio` giảm đột ngột; tỉ lệ feedback 👎 của người dùng.
  - **Chi phí:** alert theo **cost / request** (bắt việc đổi model hoặc prompt phình to) và theo **budget còn lại trong ngày** (dự báo cạn budget trước khi bị 402), thay cho ngưỡng tuyệt đối $2/giờ dễ kêu nhầm khi traffic tăng.
  - **Tầng load balancer:** metric 5xx và upstream latency của Nginx/ingress, cộng một synthetic check từ bên ngoài gọi `/ask` mỗi phút, để bắt các lỗi 502/504 mà metric của agent không thấy.
  - **SLO theo error budget:** alert kiểu *multi-window burn rate* (ví dụ đốt hết 2% budget trong 1 giờ) thay vì ngưỡng tức thời, để vừa bắt sự cố nhanh vừa ít báo động giả.
  - **Saturation:** alert khi `agent_in_flight_requests` hoặc CPU một instance tiến gần giới hạn, làm tín hiệu scale.
  - **LLM upstream:** đếm lỗi theo loại (`429`, `503`, timeout) và p99 LLM theo model, để phân biệt lỗi của mình với lỗi của provider.

---

## Phụ lục — Exercise 7.1: Đọc metrics của agent

Gọi `POST /ask` một lần rồi `curl -s http://localhost:8002/metrics | grep -v "^#"` (agent v2, WSL2):

| Loại | Ví dụ trong output | Ý nghĩa |
|---|---|---|
| **Counter** (chỉ tăng) | `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{model="mock-llm-small",type="input"} 91.0`, `llm_cost_usd_total{model="mock-llm-small"} 4.665e-05` | Đếm dồn; đọc tốc độ bằng `rate()` |
| **Gauge** (lên xuống) | `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` | Giá trị tức thời: số request đang xử lý (saturation), thông tin phiên bản |
| **Histogram** | `http_request_duration_seconds_bucket{le="0.3",method="POST",route="/ask"} 1.0` … `_count 1.0`, `_sum 0.2029` | Đếm số quan sát rơi vào từng bucket; tính percentile bằng `histogram_quantile()` |

**Câu 2: nếu label `route` dùng URL thật thay vì `route.path`.**
- Mỗi URL khác nhau tạo một tổ hợp label mới, tức **một time series mới**. Bot quét hàng nghìn URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`...) sẽ sinh hàng nghìn series cho `http_requests_total` và cho mỗi bucket của histogram (12 bucket + `_sum` + `_count` mỗi URL). Đây là **bùng nổ cardinality**: Prometheus tốn RAM và đĩa, query chậm, có thể bị OOM. Kẻ tấn công còn có thể cố ý làm sập hệ thống giám sát theo cách này.
- Dùng route template thì mọi URL không khớp route gom vào **một** series. Đã thử gọi 3 URL lạ, mỗi cái trả 404, và `/metrics` chỉ có thêm đúng một dòng: `http_requests_total{method="GET",route="unmatched",status="404"} 3.0`.

**Nhiệm vụ: thêm metric `agent_question_length_chars`.** Đã thêm vào `develop/app.py`:

```python
QUESTION_LENGTH = Histogram(
    "agent_question_length_chars", "Độ dài câu hỏi gửi tới /ask (ký tự)",
    buckets=(20, 50, 100, 200, 500, 1000, 2000),
)
# trong ask(), trước khi gọi LLM:
QUESTION_LENGTH.observe(len(body.question))
```

Output sau 1 câu hỏi dài 40 ký tự: `agent_question_length_chars_bucket{le="50.0"} 1.0`, `agent_question_length_chars_count 1.0`, `agent_question_length_chars_sum 40.0`.

Metric này dùng **Histogram**, không dùng label `length=...`: độ dài có vô số giá trị, nếu làm label thì cardinality cũng bùng nổ như câu 2. Histogram chỉ tạo 7 bucket cố định nhưng vẫn tính được phân bố và percentile.

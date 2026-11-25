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

> TODO (Bước 2): điền sau khi chạy stack Docker.

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): `screenshots/grafana-dashboard.png`
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): `screenshots/jaeger-slow-trace.png`
- [ ] Một trace lỗi trong Jaeger (có event `exception`): `screenshots/jaeger-error-trace.png`
- [ ] Trang Alerts của Prometheus với ít nhất 2 alert đang **firing**: `screenshots/prometheus-alert-firing.png`

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

> TODO (sau Bước 2)

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...

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

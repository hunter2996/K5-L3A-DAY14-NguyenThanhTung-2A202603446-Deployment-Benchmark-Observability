# Benchmark & Observability Report — Day 14

> Mọi con số trong báo cáo này đều lấy trực tiếp từ các file JSON trong `reports/`, CSV trong `benchmark-results/`, Prometheus API và ảnh chụp trong `screenshots/`.

**Họ tên:** Nguyễn Thanh Tùng (MSSV: 2A202603446)
**Ngày chạy:** 2026-11-25
**Môi trường:** Windows 11, Python 3.12.10, Docker Desktop 29.5.2 (WSL2 Linux backend), Docker Compose v5.1.3

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | `< 500 ms` | Với mock LLM cơ bản ~120–180ms + retrieval ~20ms, 95% request thông thường hoàn thành dưới 250–350ms; ngưỡng 500ms đảm bảo trải nghiệm chat mượt trong khi vẫn chừa biên cho dao động tải bình thường và bắt được suy giảm tail latency khi `MOCK_SLOW_RATE` tăng hoặc quá tải concurrency. |
| Error rate `/ask` | `< 2.0 %` | Mock upstream có ~1% lỗi ngẫu nhiên (`MOCK_ERROR_RATE=0.01`); đặt SLO 2% (error budget 2%) chấp nhận lỗi thoáng qua của upstream nhưng phát hiện sớm sự cố hệ thống (alert đặt chặt hơn về thời gian: > 5% kéo dài 1 phút để tránh alert fatigue). |
| Accuracy trên golden dataset | `≥ 85 %` (mục tiêu sau sửa KB: `100 %`, 0 regression) | Đảm bảo agent trả lời đúng đa số câu hỏi kỹ thuật cốt lõi trong bộ 23 câu `golden_dataset.jsonl` và chặn mọi thay đổi làm giảm chất lượng so với baseline trên CI (`.github/workflows/eval.yml`). |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 requests, `MOCK_SLOW_RATE=0.03`)

Nguồn dữ liệu: `reports/load_v2_c1.json`, `reports/load_v2_c10.json`, `reports/load_v2_c50.json`, `reports/load_v2_c100.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO (`p95 ≤ 500ms`, `err ≤ 2%`) |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 3.97 | 201.6 | 235.0 | 1710.5 | 0.67% (`2/300`) | ✅ PASS |
| 10 | 37.68 | 197.4 | 234.8 | 1699.3 | 1.67% (`5/300`) | ✅ PASS |
| 50 | 131.51 | 210.1 | 362.8 | 1717.4 | 1.00% (`3/300`) | ✅ PASS |
| 100 | 53.28 | 449.0 | 3310.3 | 3840.7 | 0.33% (`1/300`) | ❌ FAIL (p95 > 500ms) |

**Nhận xét:** RPS **không tăng tuyến tính** theo concurrency. Từ `c=1` lên `c=10`, RPS tăng gần tuyến tính (`3.97 → 37.68 req/s`, ~9.5×) vì workload chủ yếu chờ I/O async (`asyncio.sleep`). Lên `c=50`, RPS tiếp tục tăng mạnh lên `131.51 req/s` nhưng p95 bắt đầu nhích lên `362.8ms`. Tới `c=100`, đơn tiến trình uvicorn trên Windows chạm điểm bão hoà hàng đợi socket / event-loop contention khiến latency xếp hàng tăng vọt (`p50=449ms`, `p95=3310.3ms`, `p99=3840.7ms`) và throughput **sụt ngược** xuống `53.28 req/s` (định luật Little & hiện tượng thrashing khi vượt ngưỡng bão hoà).

### 2.2 Tail latency (`concurrency=20`, `300` requests)

Nguồn dữ liệu: `reports/load_v2_slow_0.json`, `reports/load_v2_slow_0.03.json`, `reports/load_v2_slow_0.1.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) | RPS | SLO |
|---|---:|---:|---:|---:|---|
| `0` | 193.3 | 226.9 | 240.7 | 99.40 | ✅ PASS |
| `0.03` (mặc định) | 212.4 | 231.7 | 1672.9 | 71.01 | ✅ PASS |
| `0.1` | 306.1 | 1679.3 | 1705.2 | 51.77 | ❌ FAIL |

**Nhận xét:**
- **`p99` và `p95` nhạy nhất** với nhóm request chậm bất thường: khi `MOCK_SLOW_RATE` tăng từ `0` lên `0.03`, `avg` chỉ nhích nhẹ **+19.1ms** (`193.3 → 212.4ms`) và `p95` gần như đứng yên (`226.9 → 231.7ms`), nhưng **`p99` nhảy vọt ~7 lần lên `1672.9ms`**. Khi tăng lên `0.1` (> 5% request bị chậm), cả **`p95` cũng bật lên `1679.3ms`** (+1452ms) trong khi `avg` mới chỉ là `306.1ms`.
- **Vì sao SLO không nên đặt theo average?** Giá trị trung bình bị 90–97% request nhanh kéo xuống nên **che giấu trải nghiệm tệ của nhóm người dùng đuôi dài (tail latency)**. Đặt SLO theo `p95`/`p99` phản ánh đúng trải nghiệm xấu nhất mà 5% hoặc 1% người dùng đang chịu đựng.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn dữ liệu: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json` (bộ 23 câu hỏi `golden_dataset.jsonl` không chỉnh sửa).

| Chỉ số | Agent v1 (`eval_v1.json`) | Agent v2 (`eval_v2.json`) | Agent v2 sau khi sửa KB (`eval_v2_fixed.json`) |
|---|---:|---:|---:|
| Accuracy | 4.35% (`1/23`) | 91.3% (`21/23`) | **100.0% (`23/23`)** |
| Avg tokens / câu | 57.0 | 109.0 | 114.4 |
| Tổng cost (USD) | `$0.000403` | `$0.000759` | `$0.000797` |
| Cost / câu đúng (USD) | `$0.00040335` | `$0.00003616` | **`$0.00003466`** |
| Latency p50 / p95 (ms) | `191.9 / 1488.7` | `199.2 / 234.1` | `206.6 / 240.9` |
| Regression so với baseline | — | `0` (vs v1, +20 câu cải thiện) | **`0`** (vs v2, +2 câu cải thiện) |
| Quality gate | ❌ FAIL | ✅ PASS (`≥ 80%`) | ✅ **PASS** (`--min-accuracy 1.0`) |

**Những câu v2 trả lời sai ban đầu và cách sửa** (chỉ thêm vào `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py` và đồng bộ sang `production/utils/mock_llm.py`, **không sửa** `golden_dataset.jsonl`):

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm entry với từ khoá `["jwt"]` giải thích API key cố định không tự hết hạn còn JWT là token có chữ ký số chứa thông tin user và thời điểm hết hạn, xác minh stateless. |
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm entry với từ khoá `["hpa", "kubernetes", "autoscal"]` giải thích Kubernetes HPA tự động scale pod dựa trên CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi). |

**Nhận xét trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- **Chất lượng:** v2 (có bước RAG retrieval) nâng độ chính xác từ `4.35%` (`1/23`) lên `91.3%` (`21/23`) và đạt `100%` (`23/23`) sau khi bổ sung 2 tài liệu thiếu — không có câu nào bị regression.
- **Chi phí:** v2 tốn ~1.9–2.0× số token/câu (`57.0 → 109.0 → 114.4`) do phải đính kèm ngữ cảnh retrieve vào prompt, làm tổng cost 23 câu tăng gần gấp đôi (`$0.000403 → $0.000797`). Tuy nhiên xét theo chỉ số **cost / câu đúng**, v2 (`$0.00003466`) **rẻ hơn ~11.6 lần** so với v1 (`$0.00040335`) vì v1 đốt tiền cho 22/23 câu trả lời sai.
- **Latency:** v2 chậm hơn v1 khoảng ~20–30ms ở p50 (`191.9ms → 199.2–206.6ms`) do thêm bước `search_knowledge_base` (~20ms) và prompt dài hơn — đây là đánh đổi rất đáng giá so với mức tăng +95.65 điểm % độ chính xác.

**Quality gate trong CI (Exercise 7.3):**
- Đã tạo workflow [.github/workflows/eval.yml](.github/workflows/eval.yml) chạy tự động trên mỗi `pull_request` vào nhánh `main`: khởi động agent v2 (`MOCK_ERROR_RATE=0`) và chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85`.
- **Cơ chế chặn merge:** `run_eval.py` trả **exit code `0`** khi `accuracy >= min_accuracy` và `regressions == 0`, ngược lại trả **exit code `1`** làm GitHub Actions job fail và chặn merge PR. Đã kiểm thử thực tế bằng cách chạy v1 với `--baseline reports/eval_v2.json`: script phát hiện **20 câu bị regression**, in `Quality gate: ❌ FAIL` và thoát với **`exit code 1`**.

---

## 4. Observability stack (production)

### 4.1 Screenshot & trả lời Exercise 7.4

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với **4/4 alerts đang FIRING đồng thời**: [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png)

**Trả lời 4 câu hỏi Exercise 7.4 (số liệu thực tế từ Grafana / Prometheus):**
1. **p95 và avg:** Tại thời điểm chụp Grafana đang có tải (30 user sau đợt scale test), `Latency p95 = 294.54 ms`, `Error rate = 0.46%`, `Traffic = 11.9 req/s`; query trực tiếp trên Prometheus trong cửa sổ 5 phút cho `p95 = 293.8 ms`, `avg = 233.9 ms`, trong khi đường `p99` trên biểu đồ duy trì quanh `~1.8s`. Khoảng cách lớn giữa `avg`/`p95` (~234–294ms) với `p99` (~1.8s) cho thấy hiện tượng tail latency (3% request chậm do `MOCK_SLOW_RATE=0.03`).
2. **Requests/s theo instance:** Query `sum by (instance) (rate(http_requests_total{job="agent", route="/ask"}[5m]))` cho `172.21.0.4:8000 = 20.63 req/s` và `172.21.0.8:8000 = 20.38 req/s` → **Nginx phân tải rất đều** giữa các instance qua cơ chế round-robin + Docker DNS `resolve`.
3. **Vì sao input tokens/s lớn hơn output tokens/s?** Query `sum by (type) (rate(llm_tokens_total{job="agent"}[5m]))` ghi nhận **`input = 3,150.8 tok/s`** gần gấp đôi **`output = 1,649.6 tok/s`** (trên biểu đồ Grafana đỉnh ~7.3K input vs ~4.7K output). Lý do là ở agent v2 (RAG), mỗi request đều ghép `SYSTEM_PROMPT` + tài liệu context lấy từ knowledge base + câu hỏi người dùng thành input prompt dài, trong khi câu trả lời output ngắn gọn hơn.
4. **`curl -i http://localhost:8080/metrics` trả về gì và vì sao?** Trả về **`HTTP/1.1 404 Not Found`** từ Nginx (`nginx/1.28.3`) do `nginx/nginx.conf` cấu hình tường minh `location = /metrics { return 404; }`. `/metrics` chứa thông tin nội bộ (route, lưu lượng, model, chi phí, số instance) nên chỉ cho phép Prometheus scrape qua mạng nội bộ `obs_net`, không public ra ngoài Internet.

**Unit test alert rules (`promtool`):**
```text
docker run --rm -v ".../prometheus:/p" --entrypoint promtool prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml
Processing test file: /p/alert_rules_test.yml
  SUCCESS
```

### 4.2 Thí nghiệm "phá hệ thống" (Exercise 7.6)

Chạy trên stack `-p k5-day14-observability` với tải nền Locust 30 users; đo thời gian thực tế bằng cách poll `/api/v1/alerts` mỗi 10s:

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Giá trị khi firing & Panel phản ánh rõ nhất |
|---|---|---|---|---|
| **A** | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (`for: 1m`, critical) | **`98.3s`** (pending ở `38.1s`) | Error rate = **`21.46%`** (SLO < 5%). Panel **Error rate** chuyển đỏ & **Requests/s theo status code** (503 tăng mạnh). |
| **B** | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (`for: 2m`, warning) | **`148.3s`** (pending ở `28.1s`) | p95 = **`1.911s`** (SLO < 1s). Panel **Latency p95** chuyển đỏ & **Latency /ask — p50 / p95 / p99**. |
| **C** | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (`for: 2m`, warning) | Đã firing từ tải cao + tăng vọt ngay khi đổi model (`for: 2m`) | Burn rate tăng vọt lên **`$12.12 – $12.54/giờ`** (ngưỡng > `$2/giờ`). Panel **Cost burn rate (USD/giờ)** & **LLM cost (1h)**. |
| **D** | `docker pause k5-day14-observability-agent-2` | `AgentInstanceDown` (`for: 30s`, critical) | **`~40s`** kể từ lúc pause (pending sau ~`8s`) | `up{instance="172.21.0.10:8000"} = 0`. Panel **Instances up** giảm từ 3 → 2. |
| **E** | `AGENT_VERSION=v1` | **Không có alert nào firing** | Không firing | `agent_info` đổi sang `version="v1"`, câu hỏi HPA trả lời chung chung sai kiến thức, `tokens/s` giảm nhưng mọi panel Golden Signals vẫn xanh. |

**Phân tích bổ sung (Thí nghiệm D & E):**
- **Vì sao thí nghiệm D dùng `docker pause` thay vì `docker stop` và lỗi `504` không hiện trên panel Error rate của agent?** Khi `docker stop`, Docker DNS xoá IP container nên Prometheus bỏ hẳn target thay vì đánh dấu `up == 0`; khi `docker pause`, container vẫn còn trong DNS nhưng treo không phản hồi nên Prometheus scrape timeout → `up == 0` và `AgentInstanceDown` nổ sau 30s. Tương tự, lỗi `504 Gateway Timeout` do Nginx ngắt chờ vượt `proxy_read_timeout` xảy ra trước khi agent kịp trả response, nên metric bên trong agent không đếm được — production cần giám sát thêm ở tầng Nginx/Load Balancer và synthetic probes từ bên ngoài.
- **Thí nghiệm E không làm alert nào firing. Cách phát hiện loại sự cố chất lượng này trong production:**
  1. **Pre-deploy Quality Gate trong CI/CD:** chạy tự động `eval/run_eval.py` trên golden dataset với `--baseline` để chặn merge/deploy khi accuracy giảm hoặc có regression (như [.github/workflows/eval.yml](.github/workflows/eval.yml)).
  2. **Online evaluation & quality metrics:** lấy mẫu ngẫu nhiên (vd 1–5% traffic thật) chấm tự động bằng LLM-as-judge hoặc đối chiếu grounding/faithfulness, theo dõi **retrieval hit ratio** (`agent_retrieval_total{result="hit"}` giảm đột ngột về 0 như khi tụt từ v2 xuống v1) và đặt alert riêng cho `retrieval_hit_ratio < 0.7`.
  3. **Tín hiệu người dùng & canary release:** theo dõi tỉ lệ thumbs-down / retry / bỏ cuộc của người dùng và triển khai canary (chỉ đổ 5–10% traffic sang bản mới trước khi rollout 100%).

### 4.3 Debug bằng trace (Exercise 7.5)

**Trace chậm nhất tìm được trong Jaeger ([screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)):**

- `trace_id`: `6a7d1bf0529c7d5daffc639ae2495994`
- Tổng thời gian (`POST /ask`): **`1715.5 ms`** (hiển thị `1.7s` trên Jaeger; `agent.retrieve_context`: `18.8 ms`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**`1693.9 ms`**, chiếm **`98.7%`** tổng thời gian — xác nhận nút thắt nằm ở lời gọi upstream LLM, không phải bước retrieval hay code FastAPI)
- Dòng log JSON tương ứng (grep từ `docker compose -p k5-day14-observability logs agent` theo `trace_id`):

```json
{"ts": "2026-11-25T15:57:25.218608+00:00", "level": "INFO", "service": "observable-agent", "instance": "917941c66f45", "event": "llm_call", "trace_id": "6a7d1bf0529c7d5daffc639ae2495994", "span_id": "a64d3ee820dcda52", "request_id": "20d38298bc18", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1693.9}
{"ts": "2026-11-25T15:57:25.219088+00:00", "level": "INFO", "service": "observable-agent", "instance": "917941c66f45", "event": "http_request", "trace_id": "6a7d1bf0529c7d5daffc639ae2495994", "span_id": "a64d3ee820dcda52", "request_id": "20d38298bc18", "method": "POST", "route": "/ask", "status": 200, "ms": 1715.5}
```

**Trace lỗi đối chiếu (`error=true`, [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)):**
- `trace_id`: `a31a38d63a2abf552a4ec5ef2f8af8ea` (tổng `112.0 ms`, span `chat mock-llm-small` `85.6 ms` ghi event `exception`: `Upstream LLM error: 503 Service Unavailable (mock)`):

```json
{"ts": "2026-11-25T15:57:16.070343+00:00", "level": "INFO", "service": "observable-agent", "instance": "917941c66f45", "event": "llm_error", "trace_id": "a31a38d63a2abf552a4ec5ef2f8af8ea", "span_id": "0a7ad8bc29ef3246", "request_id": "1a6e0329ec09", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn dữ liệu: `benchmark-results/scale_1_stats.csv` và `benchmark-results/scale_3_stats.csv`.

**Theo endpoint chính `POST /ask` (và dòng `Aggregated` gồm cả `GET /health`):**

| Số instance | Phạm vi | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---:|---:|---:|---:|---|
| **1** (`scale_1_stats.csv`) | `POST /ask` | 59.89 | 190 | 240 | 1700 | 69 / 7107 (`0.97%`) |
| **1** (`scale_1_stats.csv`) | `Aggregated` | 65.68 | 190 | 230 | 1700 | 69 / 7794 (`0.89%`) |
| **3** (`scale_3_stats.csv`) | `POST /ask` | 59.98 | 190 | 230 | 1700 | 68 / 7177 (`0.95%`) |
| **3** (`scale_3_stats.csv`) | `Aggregated` | 66.07 | 190 | 220 | 1700 | 68 / 7906 (`0.86%`) |

**Giải thích kết quả:**
- **Scale có cải thiện p95 không?** Chỉ giảm rất nhẹ `10 ms` (`240ms → 230ms` ở `/ask`, `230ms → 220ms` ở `Aggregated`; avg giảm nhẹ `240.3ms → 233.9ms`), trong khi `p50` (`190ms`), `p99` (`1700ms`), `RPS` (~`60 req/s` cho `/ask`, ~`66 req/s` tổng) và tỉ lệ lỗi (~`0.95%` do `MOCK_ERROR_RATE=0.01`) hầu như **không đổi**.
- **Nút thắt nằm ở đâu?** Nút thắt nằm ở **upstream LLM latency & tail latency** (`~120–180ms` + 3% request chậm thêm `1500ms`), là tác vụ **I/O-bound (async)** chứ chưa làm bão hoà CPU của FastAPI agent, đồng thời 100 virtual user của Locust có thời gian nghỉ (`wait_time`) nên tổng lưu lượng phát ra đã ổn định quanh ~66 req/s.
- **Ý nghĩa thực tế:** Thêm replica không làm ngắn thời gian chờ 1 lời gọi LLM (muốn giảm p95/p99 phải tối ưu tầng LLM: streaming/TTFT, prompt caching, semantic cache, model routing hoặc timeout + hedged retry). Tuy nhiên scale lên 3 instance vẫn rất cần thiết cho **khả năng chịu lỗi (High Availability)** khi 1 container gặp sự cố và để nâng trần RPS khi tải vượt ngưỡng CPU/event-loop của 1 instance.

---

## 6. Kết luận

- **3 điều quan trọng rút ra về benchmark & observability:**
  1. **Hiệu năng (load test) và chất lượng (eval) là hai trục hoàn toàn độc lập:** một agent có thể có dashboard hạ tầng xanh tuyệt đối (latency thấp, error < 1%) nhưng trả lời sai gần hết như thí nghiệm E (`AGENT_VERSION=v1`, accuracy 4.35%). Cần cả load test SLO lẫn CI eval quality gate.
  2. **Average che giấu tail latency:** SLO và alert bắt buộc phải đặt theo percentile (`p95`/`p99`), còn khi so sánh phiên bản RAG phải nhìn vào **`cost / câu đúng`** chứ không chỉ tổng cost hay token/câu.
  3. **Sức mạnh của 3 trụ cột Metrics + Traces + Logs liên kết bằng `trace_id`:** Prometheus/Grafana trả lời *"có sự cố không"* (alert firing), Jaeger trả lời *"chậm/lỗi ở span nào"* (`chat mock-llm-small` chiếm 98.7% thời gian hoặc `exception` 503), và JSON logs có cùng `trace_id` cho chi tiết payload/token/cost của đúng request đó.

- **Các metric & alert nên bổ sung khi đưa agent lên production thật:**
  1. **Tầng Edge/Load Balancer (Nginx/Cloud LB):** đo `5xx` và đặc biệt `502`/`504 Gateway Timeout` ở phía ngoài agent (bắt lỗi mà metric nội bộ agent không thấy như ở thí nghiệm D).
  2. **Chất lượng & RAG:** `agent_retrieval_hit_ratio` (alert khi giảm đột ngột < 70%), điểm online eval định kỳ, tỉ lệ fallback/hallucination và feedback tiêu cực từ người dùng.
  3. **Bảo vệ & Chi phí chi tiết:** `rate_limit_rejected_total` (`429`), `budget_rejected_total` (`402`), **TTFT (Time To First Token)** cho streaming, độ trễ & tỉ lệ kết nối Redis (`redis_command_duration_seconds`), và Alertmanager kết nối Slack/PagerDuty kèm chính sách tail-based sampling cho OpenTelemetry trace khi traffic lớn.

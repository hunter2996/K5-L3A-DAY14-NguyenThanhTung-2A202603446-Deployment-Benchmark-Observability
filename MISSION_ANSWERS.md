# Day 14 Lab — Mission Answers (CP0 → CP5)

> **Lưu ý:** Toàn bộ số liệu đo (kích thước Docker image, mã HTTP, output `curl -i`, output `test_stateless.py`, output `check_production_ready.py`) đều thu thập từ các lệnh chạy thực tế trên máy làm bài. Không có email, số điện thoại hay secret thật trong tài liệu này.

---

## Part 1: Localhost vs Production (CP1)

### Exercise 1.1: Anti-patterns phát hiện trong `01-localhost-vs-production/develop/app.py`

So sánh giữa `01-localhost-vs-production/develop/app.py` và `01-localhost-vs-production/production/app.py` + `production/config.py`, phát hiện **7 anti-patterns**:

1. **Hardcode secrets trực tiếp trong mã nguồn (`develop/app.py` dòng 17–18):**
   - `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"`.
   - Khi đẩy lên Git, secret bị lộ vĩnh viễn trong lịch sử commit (xoá file sau đó cũng không thu hồi được).
   - **Cách bản production sửa:** `production/config.py` dòng 27, 32 đọc qua `os.getenv("OPENAI_API_KEY", "")`, `os.getenv("AGENT_API_KEY", "")` và có `validate()` fail-fast khi thiếu key ở môi trường production (dòng 37–46).
2. **Không có quản lý cấu hình tập trung (`develop/app.py` dòng 21–22):**
   - `DEBUG = True`, `MAX_TOKENS = 500` viết cứng trong code; muốn đổi giữa dev/staging/prod phải sửa code và deploy lại.
   - **Cách bản production sửa:** `production/config.py` dòng 14–35 gom toàn bộ cấu hình vào `@dataclass class Settings` đọc từ biến môi trường.
3. **Dùng `print()` thay cho structured logging và in thẳng secret ra stdout (`develop/app.py` dòng 33–38):**
   - `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` (dòng 34) làm lộ API key vào hệ thống log.
   - **Cách bản production sửa:** `production/app.py` dòng 28–32 dùng `logging` định dạng JSON; dòng 111–122 chỉ log metadata (`event`, `question_length`, `client_ip`), tuyệt đối không log secret.
4. **Không có health check / readiness endpoint (`develop/app.py` dòng 42–43):**
   - Gọi `GET /health` trả về `404 Not Found`. Nền tảng cloud (Railway/Render/Kubernetes) không biết container còn sống hay đã treo để tự restart.
   - **Cách bản production sửa:** `production/app.py` bổ sung `GET /health` (liveness, dòng 135–149) và `GET /ready` (readiness, trả `503` khi chưa sẵn sàng, dòng 152–161).
5. **Bind cứng `host="localhost"`, `port=8000` và bật `reload=True` (`develop/app.py` dòng 49–53):**
   - Bind `localhost` khiến container không nhận được traffic từ bên ngoài; cứng port `8000` sẽ lỗi trên cloud (nơi nền tảng inject biến `PORT`); `reload=True` tốn tài nguyên và mất ổn định trong production.
   - **Cách bản production sửa:** `production/app.py` dòng 194–198 bind `settings.host` (`0.0.0.0`), dùng `settings.port` (từ `PORT` env) và chỉ bật `reload=settings.debug`.
6. **Không xử lý vòng đời & graceful shutdown (`develop/app.py`):**
   - Khi container nhận `SIGTERM` lúc redeploy/scale-in, process tắt đột ngột làm đứt request đang xử lý và không đóng kết nối.
   - **Cách bản production sửa:** `production/app.py` dùng `@asynccontextmanager async def lifespan` (dòng 39–68) chuyển `is_ready = False` khi shutdown và đăng ký `signal.signal(signal.SIGTERM, handle_sigterm)` (dòng 180–189).
7. **Nhận `question` qua query param thay vì JSON body, thiếu CORS & validation chuẩn (`develop/app.py` dòng 30–31):**
   - Dữ liệu nhạy cảm/câu hỏi dài nằm trên URL dễ lộ trong access log và bị giới hạn độ dài URL; gửi JSON sẽ lỗi `422`.
   - **Cách bản production sửa:** `production/app.py` nhận JSON body ở `POST /ask` (dòng 102–108) và cấu hình `CORSMiddleware` (dòng 77–82).

### Exercise 1.2: Kết quả chạy thực tế bản `develop` và `production`

*(Ghi chú: cổng `8000` trên máy đang được một container khác dùng nên khi chạy local đổi cổng kiểm thử sang `18001`.)*

**Bản `01-localhost-vs-production/develop`:**
- Gọi `GET /`:
```http
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"message":"Hello! Agent is running on my machine :)"}
```
- Gọi `POST /ask?question=Hello` (nhận qua query param):
```http
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```
- Gọi `GET /health`:
```http
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```
- Log stdout in thẳng secret ra terminal:
```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
```

**Bản `01-localhost-vs-production/production` (`PORT=18001`):**
- `GET /health` → `200 OK`:
```json
{"status":"ok","uptime_seconds":1.2,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T13:32:00.748402+00:00"}
```
- `GET /ready` → `200 OK`: `{"ready":true}`
- `POST /ask` với JSON `{"question":"Hello"}` → `200 OK`:
```json
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","model":"gpt-4o-mini"}
```

### Exercise 1.3: Bảng so sánh Develop vs Production

| Feature | Develop (`develop/app.py`) | Production (`production/app.py` + `config.py`) | Tại sao quan trọng? |
|---|---|---|---|
| **Config & Secrets** | Hardcode `OPENAI_API_KEY`, `DATABASE_URL`, `DEBUG=True` trong code | Đọc toàn bộ từ biến môi trường (`os.getenv`) qua `Settings` + `.env.example`, có `validate()` fail-fast | Tránh lộ secret trên Git; cùng 1 artifact có thể chạy ở dev/staging/prod chỉ bằng cách đổi env (12-Factor App Factor III). |
| **Health & Readiness** | Không có (`GET /health` → `404`) | Có `GET /health` (liveness) và `GET /ready` (readiness trả `503` khi chưa sẵn sàng) | Nền tảng cloud dựa vào `/health` để tự khởi động lại container bị treo và dựa vào `/ready` để không điều hướng traffic vào instance chưa sẵn sàng. |
| **Logging** | `print("[DEBUG] ...")`, in cả `OPENAI_API_KEY` | Structured JSON logging qua `logging`, không log secret | Log JSON dễ parse/lọc/cảnh báo tự động trên Loki/Datadog/ELK; không in secret giúp tránh rò rỉ dữ liệu qua hệ thống log. |
| **Shutdown / Lifecycle** | Không có `lifespan` hay xử lý `SIGTERM` | Có `lifespan` (startup/shutdown) + xử lý `SIGTERM`, đặt `is_ready=False` | Đảm bảo zero-downtime deployment: request đang chạy kịp hoàn tất thay vì bị cắt ngang gây lỗi 502/504 cho người dùng. |
| **Network & Port** | Cố định `host="localhost"`, `port=8000`, `reload=True` | `host="0.0.0.0"`, `port` từ biến `PORT`, `reload` chỉ bật khi `DEBUG=true` | Container phải nghe trên `0.0.0.0` và đúng `$PORT` cloud cấp; tắt `reload` để tiết kiệm tài nguyên và tránh restart bất ngờ. |
| **API Contract & CORS** | Query param `?question=...`, không có CORS | JSON body có validation (`422`), `CORSMiddleware` kiểm soát origin | Tránh lộ nội dung câu hỏi trên URL/access log, kiểm soát bảo mật cross-origin cho trình duyệt. |

---

## Part 2: Docker (CP2)

### Exercise 2.1: 4 câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image là gì?** `python:3.11` (bản Debian + Python đầy đủ, kèm nhiều công cụ hệ thống nên rất nặng ~1 GB).
2. **Working directory là gì?** `/app` (`WORKDIR /app`), toàn bộ các lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối theo thư mục này.
3. **Tại sao `COPY requirements.txt` phải đứng trước `COPY app.py` / `COPY . .`?** Để tận dụng **Docker build layer cache**. Các layer Docker được cache tuần tự: vì `requirements.txt` ít thay đổi hơn mã nguồn, đặt `COPY requirements.txt` + `RUN pip install` lên trước giúp Docker tái sử dụng hoàn toàn layer cài thư viện khi ta chỉ sửa code Python, rút ngắn thời gian rebuild từ hàng chục giây xuống dưới 1 giây.
4. **`CMD` khác `ENTRYPOINT` thế nào?**
   - `CMD` định nghĩa lệnh (hoặc tham số mặc định) khi container khởi chạy và **dễ dàng bị ghi đè** nếu người dùng truyền lệnh ở cuối `docker run <image> <command>`.
   - `ENTRYPOINT` cố định chương trình thực thi chính của container (biến container thành một executable); các đối số truyền thêm trong `docker run` hoặc `CMD` sẽ được nối vào làm tham số cho `ENTRYPOINT` (chỉ bị ghi đè khi dùng cờ `--entrypoint`).

### Exercise 2.2: Build và chạy container `my-agent:develop`

Build từ gốc repo:
```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -d --rm --name cp2-dev-test -p 18002:8000 my-agent:develop
```
Kết quả gọi thực tế vào container:
- `GET http://127.0.0.1:18002/health`:
```http
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"status":"ok","uptime_seconds":2.6,"container":true}
```
- `POST http://127.0.0.1:18002/ask?question=What%20is%20Docker?`:
```http
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

### Exercise 2.3: Multi-stage build và so sánh dung lượng image

- **Stage 1 (`builder` — `FROM python:3.11-slim AS builder`):** cài đặt công cụ biên dịch (`gcc`, `libpq-dev`) và chạy `pip install --no-cache-dir --user -r requirements.txt` vào `/root/.local`.
- **Stage 2 (`runtime` — `FROM python:3.11-slim AS runtime`):** bắt đầu từ image `slim` sạch hoàn toàn, tạo non-root user `appuser`, chỉ `COPY --from=builder /root/.local /home/appuser/.local` cùng mã nguồn (`main.py`, `utils/mock_llm.py`), bổ sung `HEALTHCHECK` và chạy `uvicorn` với 2 workers.
- **Vì sao image nhỏ hơn nhiều?** Stage cuối không mang theo hệ điều hành full Debian của `python:3.11`, cũng bỏ toàn bộ compiler (`gcc`, `libpq-dev`, headers) và cache build của Stage 1 — chỉ giữ lại Python slim runtime và các package cần để chạy, đồng thời giảm bề mặt tấn công (attack surface).

**Kết quả đo thực tế bằng `docker images`:**
```text
REPOSITORY:TAG                 IMAGE ID       SIZE
my-agent:advanced              244788e0c97b   240MB
my-agent:develop               f0862306f673   1.67GB
```
- **Develop (`my-agent:develop`):** `1.67 GB` (`1,670 MB`)
- **Production (`my-agent:advanced`):** `240 MB`
- **Chênh lệch:** giảm **`1,430 MB`**, tương đương **nhỏ hơn `85.6%`** (nếu đo theo `docker image inspect .Size` trên containerd store: `406.6 MB` → `54.7 MB`, giảm `86.5%`).

### Exercise 2.4: Docker Compose stack (`02-docker/production/docker-compose.yml`)

**Sơ đồ kiến trúc stack (4 services trên bridge network `internal`):**
```text
                     ┌────────────────────────────────────────────────────────┐
                     │            Docker network: internal (bridge)           │
                     │                                                        │
Client ──(:80/:443)──► Nginx (reverse proxy,          ┌──► Redis (:6379)      │
                     │        rate limit 10r/s,       │    [vol: redis_data]  │
                     │        security headers)       │                       │
                     │         │                      │                       │
                     │         └──► Agent (:8000) ────┤                       │
                     │              (no host port,    │                       │
                     │               2 workers)       └──► Qdrant (:6333)     │
                     │                                     [vol: qdrant_data] │
                     └────────────────────────────────────────────────────────┘
```

- **Các service được khởi động:** `nginx`, `agent`, `redis`, `qdrant`.
- **Cách giao tiếp:** dùng chung bridge network `internal`; các service tìm nhau bằng tên service qua Docker internal DNS (`redis://redis:6379/0`, `http://qdrant:6333`, upstream `agent:8000`). Chỉ `nginx` mở cổng `80/443` ra host; `agent` chờ `redis` và `qdrant` đạt `condition: service_healthy` mới khởi động.
- **Kết quả chạy thực tế (`docker compose -p k5-day14-docker up -d --build`) và test qua Nginx:**
  - `GET http://127.0.0.1/health`:
  ```http
  HTTP/1.1 200 OK
  Server: nginx
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  X-XSS-Protection: 1; mode=block
  Content-Type: application/json

  {"status":"ok","uptime_seconds":5.8,"version":"2.0.0","timestamp":"2026-11-25T13:34:04.616212"}
  ```
  - `POST http://127.0.0.1/ask` (`{"question":"Explain microservices"}`):
  ```http
  HTTP/1.1 200 OK
  Server: nginx
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  X-XSS-Protection: 1; mode=block
  Content-Type: application/json

  {"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
  ```
  *(Đã dọn sạch stack bằng `docker compose -p k5-day14-docker down -v`.)*

---

## Part 3: Cloud Deployment (CP6)

### Exercise 3.1 / 3.2: Deploy Agent Part 6 (`06-lab-complete/`) lên Cloud

- **Nền tảng chính:** `[Đang chờ deploy ở CP6 — Render hoặc Railway]`
- **Public URL:** `[Đang chờ URL sau khi deploy]`
- **Redis trên cloud:** `[Đang chờ cấu hình REDIS_URL trên dashboard]`
- **Screenshots:**
  - [Deployment dashboard](screenshots/dashboard.png)
  - [Service running](screenshots/running.png)
  - [Test results](screenshots/test.png)
- **Kết quả 4 lệnh tự kiểm CP6 (`/health` → 200, `/ready` → 200, `/ask` không key → 401, `/ask` có key → 200):** `[Sẽ dán ngay sau khi deploy xong]`

### So sánh `railway.toml` và `render.yaml` (Nền tảng còn lại)

| Tiêu chí | `railway.toml` (Railway) | `render.yaml` (Render Blueprint) | Lý do khác biệt & khi nào dùng |
|---|---|---|---|
| **Định dạng & phạm vi** | TOML, cấu hình cho **1 service** (`[build]`, `[deploy]`) | YAML, **Infrastructure as Code (IaC)** khai báo **nhiều resource cùng lúc** (`services`: web, worker, `redis`/Key Value, database) | Render Blueprint quản lý cả stack trong 1 file Git; Railway cấu hình per-service và ghép dịch vụ (như Redis) trên canvas/CLI. |
| **Cách build & runtime** | `[build] builder = "DOCKERFILE"` (hoặc `"NIXPACKS"` tự nhận diện Python) + `dockerfilePath` | `type: web`, `runtime: docker` (kèm `dockerfilePath`) hoặc `runtime: python` (`buildCommand`, `startCommand`) | Cả hai đều hỗ trợ build từ `Dockerfile` multi-stage của `06-lab-complete/` và tự inject biến `$PORT`. |
| **Health check & restart** | `healthcheckPath = "/health"`, `healthcheckTimeout = 30`, `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | `healthCheckPath: /health`, `autoDeploy: true`, chọn rõ `region` (vd `singapore`) và `plan: free` | Railway cho chỉnh chi tiết timeout & chính sách retry trong file; Render cho khai báo tường minh region/gói dịch vụ và auto-deploy theo branch. |
| **Quản lý biến môi trường & secret** | **Không** khai báo env/secret trong `railway.toml`; đặt qua Dashboard hoặc `railway variables set` | Khai báo danh sách `envVars` ngay trong `render.yaml`: biến thường dùng `value`, secret dùng `generateValue: true` (tự sinh) hoặc `sync: false` (nhập tay trên dashboard, không commit) | `render.yaml` tự đóng vai trò tài liệu hóa các biến bắt buộc trong Git mà vẫn an toàn không lộ secret; Railway tách hoàn toàn config vận hành khỏi file build/deploy. |
| **Gắn dịch vụ Redis** | Tạo Redis plugin trong cùng project Railway rồi reference `${{Redis.REDIS_URL}}` | Khai báo thêm dịch vụ Redis/Key Value ngay trong Blueprint hoặc nối Internal URL qua biến `REDIS_URL` (`sync: false`) | Cả hai đều kết nối Redis qua mạng nội bộ để giảm latency và không mở cổng Redis ra Internet. |

### Exercise 3.3: GCP Cloud Run CI/CD Pipeline (`cloudbuild.yaml` & `service.yaml`)

Đọc [03-cloud-deployment/production-cloud-run/cloudbuild.yaml](03-cloud-deployment/production-cloud-run/cloudbuild.yaml) và [service.yaml](03-cloud-deployment/production-cloud-run/service.yaml), pipeline CI/CD tự động khi push lên nhánh `main` đi qua 4 bước tuần tự (`waitFor`):

1. **Test (`test`):** chạy container `python:3.11-slim`, cài `requirements.txt` + `pytest` và chạy `pytest tests/`; nếu test lỗi thì dừng pipeline ngay, không build/deploy.
2. **Build (`build`):** dùng `gcr.io/cloud-builders/docker` build image gắn 2 tag (`$COMMIT_SHA` để truy vết/rollback và `latest`), tận dụng `--cache-from=...:latest` để tăng tốc build.
3. **Push (`push`):** đẩy toàn bộ các tag của image vừa build lên Google Container Registry (`gcr.io/$PROJECT_ID/ai-agent`).
4. **Deploy (`deploy`):** dùng `gcloud run deploy` triển khai đúng image theo `$COMMIT_SHA` lên Cloud Run region `asia-southeast1` với cấu hình production (`min-instances=1` tránh cold start, `max-instances=10`, `512Mi` RAM / `1` CPU, `timeout=60s`, gắn secret từ Google Secret Manager qua `--set-secrets`). File `service.yaml` (Knative Service IaC) mô tả tương đương cấu hình khai báo: `containerConcurrency: 80`, resource requests/limits, `secretKeyRef`, `livenessProbe` (`/health`) và `startupProbe` (`/ready`).

---

## Part 4: API Security (CP3)

### Exercise 4.1: API Key Authentication (`04-api-gateway/develop/app.py`)

- **Vị trí kiểm tra:** dependency `verify_api_key` (dòng 39–54) đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` và gắn vào `/ask` bằng `Depends(verify_api_key)` (dòng 70). `/health` và `/` giữ public.
- **Khi thiếu hoặc sai key:** thiếu key → `401 Unauthorized`; sai key → `403 Forbidden` (ở Part 6 chuẩn hóa cả hai trường hợp về `401`).
- **Cách rotate key không gây downtime:** hỗ trợ danh sách nhiều key hợp lệ cùng lúc trong biến môi trường/Secret Manager (ví dụ `AGENT_API_KEYS="old-key,new-key"`), cấp `new-key` cho client chuyển đổi dần, theo dõi log đến khi không còn request dùng `old-key` rồi mới xoá `old-key`.

**Kết quả test thực tế `curl -i` (`AGENT_API_KEY=secret-key-123`):**
- Không gửi `X-API-Key` → **401**:
```http
HTTP/1.1 401 Unauthorized
server: uvicorn
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}
```
- Sai `X-API-Key` → **403**:
```http
HTTP/1.1 403 Forbidden
server: uvicorn
content-type: application/json

{"detail":"Invalid API key."}
```
- Đúng `X-API-Key: secret-key-123` → **200**:
```http
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT Authentication (`04-api-gateway/production/auth.py`)

- **Flow JWT:** Client gửi `username`/`password` tới `POST /auth/token` → server xác thực và ký JWT (HS256) chứa `sub` (username), `role`, `iat`, `exp` (hết hạn sau 60 phút) → client gửi `Authorization: Bearer <token>` ở các request sau → `verify_token` kiểm tra chữ ký và hạn dùng stateless (không cần query DB mỗi request).
- **Kết quả test thực tế (`student` / `demo123`):**
  - Không gửi token → **401**:
  ```http
  HTTP/1.1 401 Unauthorized
  www-authenticate: Bearer
  x-content-type-options: nosniff
  x-frame-options: DENY
  x-xss-protection: 1; mode=block
  referrer-policy: strict-origin-when-cross-origin

  {"detail":"Authentication required. Include: Authorization: Bearer <token>"}
  ```
  - Lấy token từ `POST /auth/token` (`{"username":"student","password":"demo123"}`):
    `access_token`: `eyJhbGciOiJIUzI1...<REDACTED>`
  - Gọi `POST /ask` với `Authorization: Bearer eyJhbGciOiJIUzI1...<REDACTED>` → **200**:
  ```http
  HTTP/1.1 200 OK
  x-content-type-options: nosniff
  x-frame-options: DENY
  x-xss-protection: 1; mode=block
  referrer-policy: strict-origin-when-cross-origin

  {"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}
  ```

### Exercise 4.3: Rate Limiting (`04-api-gateway/production/rate_limiter.py`)

- **Thuật toán:** **Sliding Window** dùng `deque` lưu timestamp từng request của mỗi user; mỗi lần kiểm tra sẽ xoá các timestamp cũ hơn `now - 60s`, nếu `len(window) >= max_requests` thì trả `429 Too Many Requests` kèm `Retry-After` và các header `X-RateLimit-*`.
- **Giới hạn:** `student` (`role="user"`) = **10 req/phút** (`rate_limiter_user`), `teacher` (`role="admin"`) = **100 req/phút** (`rate_limiter_admin`).
- **Cách bypass/nới limit cho admin:** chọn limiter theo `user["role"]` trong JWT (như `app.py` dòng 141) hoặc bỏ qua bước `limiter.check()` nếu `role == "admin"` / internal service token.

**Kết quả thực tế vòng lặp 12 request liên tiếp bằng token của `student`:**
```text
Request 1  -> HTTP 200 (requests_remaining: 9)
Request 2  -> HTTP 200
Request 3  -> HTTP 200
Request 4  -> HTTP 200
Request 5  -> HTTP 200
Request 6  -> HTTP 200
Request 7  -> HTTP 200
Request 8  -> HTTP 200
Request 9  -> HTTP 200
Request 10 -> HTTP 200
Request 11 -> HTTP 429 (chi tiết bên dưới)
Request 12 -> HTTP 429
```
Output đầy đủ `curl -i` ở Request 11:
```http
HTTP/1.1 429 Too Many Requests
server: uvicorn
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795613631
retry-after: 59
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost Guard

- **Tại sao cần cả Rate Limit lẫn Cost Guard?**
  - **Rate Limit** bảo vệ **tốc độ/tài nguyên hệ thống** (chống spam/DoS theo số request/phút).
  - **Cost Guard** bảo vệ **ngân sách tiền LLM** theo số token thực tế (1 request prompt cực dài có thể đắt gấp hàng chục lần request ngắn; người dùng gửi đều đặn dưới ngưỡng rate limit cả tháng vẫn có thể làm cạn ngân sách).
- **Kiểm tra thực tế `GET /me/usage` sau 10 request của `student`:**
```json
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,"budget_remaining_usd":0.999809,"budget_used_pct":0.0}
```
- **Thiết kế production với Redis (đã triển khai ở Part 6 `06-lab-complete/app/cost_guard.py`):**
  1. Trước khi gọi LLM (`check_budget()`): đọc tổng chi tiêu ngày (`cost:daily:YYYY-MM-DD`) và tháng (`cost:monthly:YYYY-MM`) trong Redis; nếu vượt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` thì chặn ngay bằng **`402 Payment Required`**.
  2. Sau khi gọi LLM (`record_cost(input_tokens, output_tokens)`): tính chi phí theo đơn giá input/output token rồi cộng dồn atomic bằng `INCRBYFLOAT` kèm `EXPIRE` (2 ngày cho khóa ngày, 35 ngày cho khóa tháng) để dữ liệu tự reset theo chu kỳ và dùng chung giữa mọi worker/instance.

---

## Part 5: Scaling & Reliability (CP4)

### Exercise 5.1: Health check (`/health`) vs Readiness (`/ready`)

- **`GET /health` (Liveness probe):** trả lời *"Process còn sống hay bị treo/deadlock?"*. Phải luôn trả `200` chừng nào process còn hoạt động, **kể cả khi Redis/DB tạm mất kết nối** — nếu trả lỗi khi Redis sập, orchestrator sẽ liên tục restart container gây crash loop vô ích.
- **`GET /ready` (Readiness probe):** trả lời *"Instance này đã sẵn sàng nhận traffic chưa?"*. Kiểm tra kết nối dependency (Redis/DB) và cờ `_is_ready`; trả `503 Service Unavailable` khi đang khởi động, mất Redis hoặc đang shutdown để load balancer tạm loại instance khỏi pool mà không kill container.
- **Kết quả test thực tế `05-scaling-reliability/develop`:**
  - `GET /health` → `200 OK`: `{"status":"degraded","uptime_seconds":1.3,"version":"1.0.0","environment":"development","checks":{"memory":{"status":"degraded","used_percent":96.5}}}`
  - `GET /ready` → `200 OK`: `{"ready":true,"in_flight_requests":1}`
  - `POST /ask?question=Hello` → `200 OK`

### Exercise 5.2: Graceful Shutdown (`SIGTERM`)

- Khi nền tảng deploy phiên bản mới hoặc dừng container (`docker stop`), nó gửi `SIGTERM` trước rồi chờ grace period (mặc định 10s ở Docker, 30s ở Kubernetes) trước khi gửi `SIGKILL`.
- Quy trình đúng (như `05-scaling-reliability/develop/app.py` và Part 6 `06-lab-complete/app/main.py`):
  1. Đặt cờ `_is_ready = False` → `/ready` trả `503` để load balancer ngừng đẩy request mới vào.
  2. Uvicorn ngừng nhận kết nối mới nhưng giữ vòng lặp cho các request đang chạy hoàn tất trả `200`.
  3. Đóng tài nguyên (Redis connection pool) trong phần teardown của `lifespan`.
  4. Thoát sạch với **exit code `0`** (không phải `137` do bị `SIGKILL`).

### Exercise 5.3: Stateless Design (Redis vs RAM)

- Nếu lưu conversation history / rate limit / cost trong biến `dict` ở RAM của process: khi scale ra 3 instances sau load balancer, request 1 vào Instance A lưu state trong RAM của A, request 2 đi qua Instance B sẽ **mất sạch state** (và khi restart/deploy toàn bộ state bị xoá).
- Giải pháp: thiết kế ứng dụng hoàn toàn **stateless**, đẩy mọi shared state (`session:<id>`, rate limit sliding window, tổng chi phí) ra **Redis** dùng chung với TTL hợp lý.

### Exercise 5.4 & 5.5: Load Balancing (3 instances + Nginx + Redis) & `test_stateless.py`

Chạy trong `05-scaling-reliability/production/` với tên project riêng:
```bash
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```
**Trạng thái container (`docker compose -p k5-day14-scaling ps`):**
```text
NAME                       IMAGE                    SERVICE   STATUS                    PORTS
k5-day14-scaling-agent-1   k5-day14-scaling-agent   agent     Up 9 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   k5-day14-scaling-agent   agent     Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   k5-day14-scaling-agent   agent     Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   nginx:alpine             nginx     Up 8 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   redis:7-alpine           redis     Up 21 seconds (healthy)   6379/tcp
```

**Kết quả thực tế `python test_stateless.py` (gọi qua Nginx cổng `8080`, endpoint `POST /chat`):**
```text
============================================================
Stateless Scaling Demo
============================================================

Session ID: 878ef246-473c-4756-acf9-d2dc992a0b71

Request 1: [instance-8dd5aa]
  Q: What is Docker?
  A: Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!...

Request 2: [instance-a9d6dc]
  Q: Why do we need containers?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

Request 3: [instance-33aa5e]
  Q: What is Kubernetes?
  A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

Request 4: [instance-8dd5aa]
  Q: How does load balancing work?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

Request 5: [instance-a9d6dc]
  Q: What is Redis used for?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-33aa5e', 'instance-8dd5aa', 'instance-a9d6dc'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...
  [user]: What is Kubernetes?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...
  [user]: How does load balancing work?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...
  [user]: What is Redis used for?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...

✅ Session history preserved across all instances via Redis!
```

**Kiểm tra khả năng chịu lỗi (dừng 1 container `agent` và gọi lại `POST http://127.0.0.1:8080/chat`):**
- Sau khi `docker stop` 1 trong 3 container, Nginx tự động định tuyến sang instance khỏe mạnh còn lại (`X-Served-By: 172.22.0.4:8000`, `"served_by": "instance-8dd5aa"`, HTTP `200 OK`).
- **Giải thích:** Nginx phân tải round-robin qua DNS nội bộ `agent:8000` (có `proxy_next_upstream error timeout http_503`), còn toàn bộ lịch sử hội thoại lưu tại Redis (`session:<session_id>`) nên 5 request đi qua cả 3 instance khác nhau vẫn giữ nguyên đủ 10 messages (5 user + 5 assistant) và không bị gián đoạn khi 1 instance dừng hoạt động.
- *(Đã dọn sạch stack bằng `docker compose -p k5-day14-scaling down -v`.)*

---

## Part 6: Final Project — `06-lab-complete/` (CP5)

### Tóm tắt triển khai trong `06-lab-complete/`

- `app/config.py` + `.env.example`: đọc toàn bộ cấu hình (`PORT`, `ENVIRONMENT`, `DEBUG`, `LOG_LEVEL`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`) từ biến môi trường, không hardcode secret.
- `app/redis_client.py`: tạo kết nối Redis không chặn lúc import (app vẫn khởi động được khi không có Redis), cung cấp `redis_healthy()` cho `/ready` và `close_redis()` khi shutdown.
- `app/auth.py`: kiểm tra `X-API-Key` bằng `hmac.compare_digest` so với `AGENT_API_KEY`; thiếu hoặc sai key trả về `401`.
- `app/rate_limiter.py`: sliding window 60s bằng Lua script atomic trên Redis Sorted Set (`ZREMRANGEBYSCORE`, `ZCARD`, `ZADD`, `EXPIRE`), lưu khóa theo SHA-256 prefix của API key, trả `429` kèm `Retry-After`, giữ nguyên state qua nhiều worker và sau khi restart.
- `app/cost_guard.py`: theo dõi chi phí ngày và tháng trong Redis (`INCRBYFLOAT` + `EXPIRE`), vượt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` trả `402`.
- `app/main.py`: `GET /health` luôn `200`, `GET /ready` trả `503` khi Redis mất kết nối hoặc đang shutdown, `POST /ask` tích hợp đủ auth → rate limit → cost guard → mock LLM, structured JSON logging (`JsonFormatter`), `lifespan` graceful shutdown và global exception handler không lộ stack trace.
- `Dockerfile` + `.dockerignore` + `docker-compose.yml` + `railway.toml` + `render.yaml`: multi-stage `python:3.11-slim` (image chỉ **56 MB**), non-root `appuser`, `HEALTHCHECK`, `CMD` lắng nghe `$PORT` với `exec uvicorn` làm PID 1 (`docker stop` thoát `exit code 0` trong **0.7s**).

### Kết quả chạy `python check_production_ready.py` (môi trường Linux POSIX + Docker, bản đầy đủ)

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

*(Chi tiết nổi bật từ lần chạy: Docker build 40s, image size `56 MB < 500 MB`, Rate limit trả `429` sau 5 request thành công và giữ `429` sau restart nhờ Redis, Readiness trả `503` khi mất Redis trong khi `/health` vẫn `200`, Cost guard trả `402`, 18 dòng log JSON hợp lệ, `SIGTERM` lúc đang có request trả `200` + `exit code 0`, `docker stop` thoát `exit code 0` trong `0.7s`.)*

---

## AI Log

Theo quy định tại `RULES.md`:

| Giai đoạn | Hỏi / Yêu cầu AI (Antigravity) | Nhận được gì | Tự kiểm tra & xác minh thế nào |
|---|---|---|---|
| **CP0** | Quét toàn bộ dự án, lập danh sách CP0–CP8 và kiểm tra điều kiện môi trường | Phát hiện tên repo lệch mẫu `SUBMISSION.md`, thiếu distro WSL2 Ubuntu trên máy Windows, và kiểm tra sẵn Docker/Python | Tự chạy `docker run --rm hello-world`, `python --version`, `wsl -l -v`, `gh repo view` để xác nhận môi trường thực tế |
| **CP1–CP4** | Hướng dẫn chạy từng ví dụ `develop` / `production`, tổng hợp câu trả lời theo rubric | Các lệnh chạy thực tế, giải thích kiến trúc và bảng so sánh anti-patterns / Docker / JWT / Rate Limit / Stateless | Chạy trực tiếp từng app và Docker Compose stack (`-p k5-day14-docker`, `-p k5-day14-scaling`), đo dung lượng thật bằng `docker images` (`1.67GB` vs `240MB`), gọi `curl -i` thu 401/403/200/429 và chạy `test_stateless.py` xác nhận 3 instances + 10 messages trong Redis |
| **CP5** | Hỗ trợ viết 12 file trong `06-lab-complete/` theo đúng hợp đồng 12 tiêu chí (không sửa `check_production_ready.py`) | Code hoàn chỉnh cho `app/*.py`, multi-stage `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy nguyên bản `check_production_ready.py` cả bản tĩnh (`28/28`), trên Windows (`42/42`, xác nhận `⚠️` chỉ do `os.name == 'nt'`) và trong container Linux POSIX có Docker CLI đạt **`PASS 12/12` (43/43 checks passed)** |

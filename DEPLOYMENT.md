# Deployment Information

## Public URL
https://agent-production-1f93d101.up.railway.app

## Platform
Railway (`06-lab-complete/` deployed with Docker multi-stage build + Railway managed Redis service)

## Test Commands

Đặt biến môi trường trước khi chạy (thay `<YOUR_AGENT_API_KEY>` bằng key đã cấu hình trong Railway Dashboard):

```bash
export BASE="https://agent-production-1f93d101.up.railway.app"
export AGENT_API_KEY="<YOUR_AGENT_API_KEY>"
```

### 1. Health Check (Liveness — expected `200`)
```bash
curl -sS "$BASE/health"
# {"status":"ok","environment":"production","uptime_seconds":...}
```

### 2. Readiness Check (Redis connected — expected `200`)
```bash
curl -sS "$BASE/ready"
# {"status":"ready","redis":"connected"}
```

### 3. Authentication Check — thiếu `X-API-Key` (expected `401`)
```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello"}'
# 401
```

### 4. API Test — có `X-API-Key` hợp lệ (expected `200`)
```bash
curl -sS -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
# {"question":"Hello","answer":"...","user_id":"test","usage":{...}}
```

### 5. Bộ 4 lệnh tự kiểm CP6 (chỉ in mã HTTP, không lộ key)
```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

## Environment Variables Set

Tất cả cấu hình và secret được đặt trên **Railway Dashboard / CLI** cho service `agent`, không ghi cứng trong mã nguồn:

- `PORT` (`8000`)
- `ENVIRONMENT` (`production`)
- `LOG_LEVEL` (`INFO`)
- `REDIS_URL` (tham chiếu nội bộ `${{Redis.REDIS_URL}}` tới dịch vụ Redis cùng project Railway)
- `AGENT_API_KEY` (khóa ngẫu nhiên bí mật, chỉ lưu trong biến môi trường Railway)
- `RATE_LIMIT_PER_MINUTE` (`10`)
- `DAILY_BUDGET_USD` (`1.0`)
- `MONTHLY_BUDGET_USD` (`10.0`)

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)

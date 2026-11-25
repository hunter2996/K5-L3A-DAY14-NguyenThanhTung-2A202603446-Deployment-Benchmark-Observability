"""
Production AI Agent — Part 6 Final Project.
Tích hợp: 12-Factor config, Structured JSON logging, Health/Readiness probes,
API Key auth, Redis sliding-window rate limiter, Redis cost guard, Graceful shutdown.
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import close_redis, redis_healthy
from utils.mock_llm import ask as llm_ask

_STANDARD_LOG_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})


class JsonFormatter(logging.Formatter):
    """Định dạng mỗi dòng log của app thành 1 JSON object hợp lệ."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _configure_logging() -> logging.Logger:
    app_logger = logging.getLogger("app")
    app_logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    app_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    app_logger.addHandler(handler)
    app_logger.propagate = False
    return app_logger


logger = _configure_logging()

START_TIME = time.time()
_shutting_down = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời startup / graceful shutdown (SIGTERM)."""
    global _shutting_down
    _shutting_down = False
    logger.info(
        "Agent starting up",
        extra={
            "event": "startup",
            "environment": settings.environment,
            "port": settings.port,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
        },
    )
    yield
    _shutting_down = True
    logger.info("Graceful shutdown initiated", extra={"event": "shutdown_start"})
    close_redis()
    logger.info("Graceful shutdown complete", extra={"event": "shutdown_complete"})


app = FastAPI(
    title="Production AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Ghi structured JSON log cho mọi HTTP request."""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.exception(
            "Unhandled error while processing request",
            extra={
                "event": "request_error",
                "method": request.method,
                "path": request.url.path,
                "ms": elapsed_ms,
            },
        )
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "HTTP request completed",
        extra={
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": elapsed_ms,
        },
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Không lộ stack trace ra client khi có lỗi không mong muốn."""
    logger.error(
        "Internal server error",
        extra={"event": "internal_error", "path": request.url.path, "error_type": type(exc).__name__},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[str] = Field(default=None, max_length=128)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()) * 2)


@app.get("/health")
def health():
    """Liveness probe — luôn trả 200 khi process còn sống, kể cả khi Redis chưa kết nối."""
    return {
        "status": "ok",
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
    }


@app.get("/ready")
def ready():
    """Readiness probe — 200 khi sẵn sàng, 503 khi Redis mất kết nối hoặc đang shutdown."""
    if _shutting_down or not redis_healthy():
        raise HTTPException(
            status_code=503,
            detail="Service not ready: Redis unavailable",
        )
    return {"status": "ready", "redis": "connected"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    """Protected endpoint: API key → Rate limit (Redis) → Cost guard (Redis) → LLM."""
    rate_info = check_rate_limit(api_key)
    check_budget()

    answer = llm_ask(body.question)

    input_tokens = _estimate_tokens(body.question)
    output_tokens = _estimate_tokens(answer)
    request_cost = record_cost(input_tokens, output_tokens)

    logger.info(
        "Agent answered question",
        extra={
            "event": "ask_completed",
            "question_len": len(body.question),
            "answer_len": len(answer),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(request_cost, 8),
        },
    )
    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(request_cost, 8),
            "requests_remaining": rate_info["remaining"],
        },
    }

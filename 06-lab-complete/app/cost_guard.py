"""
Cost guard (tiêu chí 8) — Cộng dồn chi phí LLM vào Redis theo ngày và tháng.
Vượt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402 Payment Required.
"""
import logging
from datetime import datetime, timezone

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import get_redis

logger = logging.getLogger("app.cost_guard")

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAILY_TTL_SECONDS = 2 * 24 * 3600
MONTHLY_TTL_SECONDS = 35 * 24 * 3600

_fallback_spend: dict[str, float] = {}


def _daily_key() -> str:
    return f"cost:daily:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _monthly_key() -> str:
    return f"cost:monthly:{datetime.now(timezone.utc).strftime('%Y-%m')}"


def _get_spend(redis_key: str) -> float:
    client = get_redis()
    if client is not None:
        try:
            raw = client.get(redis_key)
            return float(raw) if raw is not None else 0.0
        except redis.RedisError as exc:
            logger.warning(
                "Redis unavailable reading cost; using fallback",
                extra={"event": "cost_guard_redis_fallback", "error": str(exc)},
            )
    return _fallback_spend.get(redis_key, 0.0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget ngày hoặc tháng → raise HTTPException(402)."""
    daily_spent = _get_spend(_daily_key())
    if daily_spent >= settings.daily_budget_usd:
        logger.warning(
            "Daily budget exceeded",
            extra={
                "event": "budget_exceeded",
                "period": "daily",
                "spent_usd": round(daily_spent, 8),
                "budget_usd": settings.daily_budget_usd,
            },
        )
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Daily budget exceeded",
                "spent_usd": round(daily_spent, 8),
                "budget_usd": settings.daily_budget_usd,
            },
        )

    monthly_spent = _get_spend(_monthly_key())
    if monthly_spent >= settings.monthly_budget_usd:
        logger.warning(
            "Monthly budget exceeded",
            extra={
                "event": "budget_exceeded",
                "period": "monthly",
                "spent_usd": round(monthly_spent, 8),
                "budget_usd": settings.monthly_budget_usd,
            },
        )
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Monthly budget exceeded",
                "spent_usd": round(monthly_spent, 8),
                "budget_usd": settings.monthly_budget_usd,
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí request vào tổng trong Redis và trả về chi phí request."""
    cost = (input_tokens / 1000.0) * PRICE_INPUT_PER_1K + (output_tokens / 1000.0) * PRICE_OUTPUT_PER_1K
    d_key = _daily_key()
    m_key = _monthly_key()

    client = get_redis()
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incrbyfloat(d_key, cost)
            pipe.expire(d_key, DAILY_TTL_SECONDS)
            pipe.incrbyfloat(m_key, cost)
            pipe.expire(m_key, MONTHLY_TTL_SECONDS)
            pipe.execute()
            return cost
        except redis.RedisError as exc:
            logger.warning(
                "Redis unavailable recording cost; using fallback",
                extra={"event": "cost_guard_redis_fallback", "error": str(exc)},
            )

    _fallback_spend[d_key] = _fallback_spend.get(d_key, 0.0) + cost
    _fallback_spend[m_key] = _fallback_spend.get(m_key, 0.0) + cost
    return cost

"""Capital.com API integration."""

from trader.integrations.capital_com.client import (
    CapitalComClient,
    CapitalComError,
    Resolution,
)
from trader.integrations.capital_com.rate_limit import RateLimitError, RedisRateLimiter
from trader.integrations.capital_com.stream import CapitalComStreamClient, OhlcUpdate
from trader.integrations.capital_com.trading import CapitalComTradingClient

__all__ = [
    "CapitalComClient",
    "CapitalComTradingClient",
    "CapitalComError",
    "CapitalComStreamClient",
    "RateLimitError",
    "RedisRateLimiter",
    "Resolution",
    "OhlcUpdate",
]

"""Trading-calendar boundaries shared by reports, risk and account context.

Capital.com reports and owner-facing Telegram messages use Riyadh time.  Keeping
the risk windows in the same timezone prevents a UTC-midnight rollover from
counting a different set of closed trades than the P&L shown to the owner.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TRADING_TIMEZONE = ZoneInfo("Asia/Riyadh")


def trading_day(now: datetime) -> tuple[str, datetime]:
    """Return the Riyadh day key and its UTC midnight instant."""

    local = now.astimezone(TRADING_TIMEZONE)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local.date().isoformat(), start.astimezone(UTC)


def trading_week(now: datetime) -> tuple[str, datetime]:
    """Return the ISO week key and its Riyadh Monday midnight instant."""

    local = now.astimezone(TRADING_TIMEZONE)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    iso = local.date().isocalendar()
    return f"{iso.year}-W{iso.week:02d}", start.astimezone(UTC)

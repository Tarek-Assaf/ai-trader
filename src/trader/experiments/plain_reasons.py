"""Plain-language wording for why the bot did not trade, for Telegram."""

from __future__ import annotations

_FRAGMENTS: tuple[tuple[str, str], ...] = (
    (
        "entries are paused",
        "Entries are paused (a safety stop is latched). Send /status, then /resume.",
    ),
    ("external pause or kill", "You paused or killed trading (/pause, /stop or /kill)."),
    ("execution has not started", "The bot was still starting up and was not ready to trade."),
    (
        "broker state mismatch",
        "Capital.com and the bot disagreed about open positions, so it stopped to be safe.",
    ),
    (
        "spread_too_wide",
        "The gap between buy and sell price (spread) was too wide to trade safely.",
    ),
    ("spread_quote_unavailable", "No live buy/sell price was available."),
    ("spread_atr_limit_exceeded", "The spread was too wide compared with recent price movement."),
    (
        "instrument_spread_ceiling_exceeded",
        "The spread was above the allowed maximum for this market.",
    ),
    (
        "stage_cap_or_sizing_missing",
        "The position size could not be worked out within the paper-stage size limit.",
    ),
    (
        "guardian_context_or_sizing_missing",
        "The risk guard did not have the account or size information it needs.",
    ),
    ("market_closed_or_in_break", "The market is closed or in its daily break."),
    ("scheduled_event_buffer", "A scheduled news event is too close."),
    ("margin", "It would have used more margin than the account limit allows."),
    ("daily_loss", "Today's loss limit has been reached."),
    ("max_open", "The maximum number of open positions is already reached."),
    ("loss_cooldown", "The bot is in its cooldown after a loss."),
    (
        "ATR is not ready",
        "The bot does not have enough recent price history yet to place the stop.",
    ),
    (
        "not enough closed bars",
        "The bot does not have enough recent price history yet to place the stop.",
    ),
    ("broker quote is unavailable", "No live buy/sell price was available from Capital.com."),
    ("needs broker rules", "The bot could not read the market's trading rules from Capital.com."),
    (
        "stop_or_target_on_wrong_side",
        "The stop or target ended up on the wrong side of the entry price.",
    ),
)


def plain_reason(raw: str) -> str:
    """Return a sentence for a known refusal code, or a generic one."""
    text = str(raw)
    lowered = text.lower()
    for fragment, sentence in _FRAGMENTS:
        if fragment.lower() in lowered:
            return sentence
    return "The bot's safety checks refused the order."


def refusal_text(epic: str, direction: str, raw: str, time_text: str) -> str:
    """The Telegram message for a signal that did not become an order."""
    return (
        f"NOT TRADED {epic} {direction}\n"
        f"Time: {time_text}\n"
        "\n"
        f"Why: {plain_reason(raw)}\n"
        "Result: no order was placed.\n"
        "\n"
        f"Detail: {str(raw)[:160]}"
    )

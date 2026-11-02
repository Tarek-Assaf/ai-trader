from trader.experiments.plain_reasons import plain_reason, refusal_text


def test_known_codes_get_plain_sentences() -> None:
    assert "paused" in plain_reason("entries are paused").lower()
    assert "spread" in plain_reason("spread_too_wide").lower()
    assert "size" in plain_reason("guardian rejected: stage_cap:stage_cap_or_sizing_missing")
    assert "history" in plain_reason("ATR is not ready for an external signal")


def test_unknown_codes_fall_back_and_message_has_the_layout() -> None:
    text = refusal_text("US100", "BUY", "something_new", "29 Oct 19:00 (Riyadh)")
    lines = text.splitlines()
    assert lines[0] == "NOT TRADED US100 BUY"
    assert "Time: 29 Oct 19:00 (Riyadh)" in lines
    assert any(line.startswith("Why: The bot's safety checks") for line in lines)
    assert "Result: no order was placed." in lines
    assert "Detail: something_new" in lines


def test_a_broker_stop_loss_refusal_says_where_the_stop_had_to_be() -> None:
    from trader.experiments.plain_reasons import plain_reason

    text = plain_reason("the broker refused the order: error.invalid.stoploss.minvalue: 3902.97")
    assert "stop-loss was too close" in text and "3902.97" in text
    assert "safety checks" not in text


def test_other_broker_refusals_are_called_the_brokers_not_the_bots() -> None:
    from trader.experiments.plain_reasons import plain_reason

    assert "Capital.com refused" in plain_reason("the broker refused the order: error.x")
    assert "take-profit" in plain_reason("the broker refused: error.invalid.takeprofit.minvalue: 9")

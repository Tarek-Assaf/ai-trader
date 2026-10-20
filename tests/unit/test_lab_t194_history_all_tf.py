import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.lab_t194_history_all_tf import _metrics, _splits  # noqa: E402


def _trade(entry: str, gross: float, net: float) -> dict[str, object]:
    return {"entry": entry, "gross_r": gross, "r": net}


def test_metrics_include_costs_and_drawdown() -> None:
    trades = [
        _trade("2020-01-01T01:00:00+00:00", 3.0, 2.0),
        _trade("2020-01-01T13:00:00+00:00", -1.0, -2.0),
    ]

    result = _metrics(trades, "r")

    assert result["trades"] == 2
    assert result["win_rate"] == 0.5
    assert result["net_r"] == 0.0
    assert result["max_drawdown_r"] == 2.0
    assert result["profit_factor"] == 1.0


def test_session_splits_use_broad_utc_tags() -> None:
    trades = [
        _trade("2020-01-01T01:00:00+00:00", 3.0, 3.0),
        _trade("2020-01-01T13:00:00+00:00", -1.0, -1.0),
    ]

    result = _splits(trades, "r", "session")

    assert result["asia"]["trades"] == 1
    assert result["new_york"]["trades"] == 1
    assert result["london"]["trades"] == 1

from __future__ import annotations

from pathlib import Path

import pytest

viz = pytest.importorskip("trader.viz")
pytest.importorskip("matplotlib")


def test_all_charts_render_and_save_png_html(tmp_path: Path) -> None:
    candles = [
        {"open": 100, "high": 105, "low": 98, "close": 103},
        {"open": 103, "high": 108, "low": 101, "close": 102},
    ]
    trade_rows = [{"index": 0, "price": 100, "kind": "entry"}]
    charts = [
        viz.candlestick(candles, trades=trade_rows, instrument="US100"),
        viz.equity_curve([{"equity": 1000}, {"equity": 1010}]),
        viz.drawdown([{"equity": 1000}, {"equity": 990}]),
        viz.session_pnl_heatmap([{"session": "NY", "pnl": 5}]),
        viz.strategy_comparison([{"strategy": "trend", "pnl": 5}]),
        viz.spread_by_session([{"session": "NY", "spread": 1.2}]),
    ]
    for index, figure in enumerate(charts):
        png = viz.save_png(figure, tmp_path / f"chart-{index}.png")
        html = viz.save_html(figure, tmp_path / f"chart-{index}.html")
        assert png.stat().st_size > 0
        assert html.stat().st_size > 0


def test_empty_data_renders_no_data_chart(tmp_path: Path) -> None:
    figure = viz.equity_curve([])
    path = viz.save_png(figure, tmp_path / "empty.png")
    assert path.stat().st_size > 0

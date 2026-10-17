"""Offline chart builders for reports and owner-facing requests.

Matplotlib is loaded lazily so trading and API processes do not import a GUI
stack at startup. A clear error is raised if it is not installed.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class VisualizationUnavailable(RuntimeError):
    """The optional headless plotting dependency is not installed."""


def _records(data: Any) -> list[Mapping[str, Any]]:
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        try:
            value = data.to_dict("records")
        except TypeError:
            value = data.to_dict(orient="records")
        return [row for row in value if isinstance(row, Mapping)]
    if isinstance(data, Mapping):
        return [data]
    return [row for row in data if isinstance(row, Mapping)]


def _plot() -> tuple[Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except Exception:
        raise VisualizationUnavailable(
            "matplotlib is required for offline chart rendering"
        ) from None
    return Figure, FigureCanvasAgg


def _figure(title: str) -> tuple[Any, Any]:
    Figure, FigureCanvasAgg = _plot()
    figure = Figure(figsize=(10, 5), constrained_layout=True)
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    axis.set_title(title)
    return figure, axis


def _empty(axis: Any) -> None:
    axis.text(0.5, 0.5, "No data", ha="center", va="center", transform=axis.transAxes)
    axis.set_axis_off()


def candlestick(
    candles: Iterable[Mapping[str, Any]],
    *,
    trades: Iterable[Mapping[str, Any]] = (),
    instrument: str = "",
) -> Any:
    """Render OHLC candles with optional entry, exit, stop and target markers."""
    rows = _records(candles)
    figure, axis = _figure(f"{instrument} price and trades".strip())
    if not rows:
        _empty(axis)
        return figure
    width = 0.65
    for index, row in enumerate(rows):
        try:
            open_price = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        color = "#2ca02c" if close >= open_price else "#d62728"
        axis.vlines(index, low, high, color=color, linewidth=1)
        axis.bar(index, close - open_price, width, bottom=open_price, color=color, alpha=0.8)
    trade_rows = _records(trades)
    for trade in trade_rows:
        try:
            x_position = float(trade.get("index", len(rows) - 1))
            value = float(trade["price"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(trade.get("kind", "trade"))
        axis.scatter([x_position], [value], label=label)
    if trade_rows:
        axis.legend(loc="best")
    axis.set_xlabel("bar")
    axis.set_ylabel("price")
    return figure


def equity_curve(points: Iterable[Mapping[str, Any]], *, title: str = "Equity curve") -> Any:
    rows = _records(points)
    figure, axis = _figure(title)
    if not rows:
        _empty(axis)
        return figure
    values = [float(row["equity"]) for row in rows if "equity" in row]
    if not values:
        _empty(axis)
        return figure
    axis.plot(range(len(values)), values, color="#1f77b4")
    axis.set_xlabel("observation")
    axis.set_ylabel("equity")
    return figure


def drawdown(points: Iterable[Mapping[str, Any]], *, title: str = "Drawdown") -> Any:
    rows = _records(points)
    figure, axis = _figure(title)
    if not rows:
        _empty(axis)
        return figure
    values = [float(row["equity"]) for row in rows if "equity" in row]
    if not values:
        _empty(axis)
        return figure
    peak = values[0]
    losses: list[float] = []
    for value in values:
        peak = max(peak, value)
        losses.append(peak - value)
    axis.fill_between(range(len(losses)), losses, color="#d62728", alpha=0.35)
    axis.set_xlabel("observation")
    axis.set_ylabel("drawdown")
    return figure


def session_pnl_heatmap(rows: Iterable[Mapping[str, Any]], *, title: str = "Session P&L") -> Any:
    data = _records(rows)
    figure, axis = _figure(title)
    if not data:
        _empty(axis)
        return figure
    sessions = [str(row.get("session", "unknown")) for row in data]
    values = [float(row.get("pnl", 0)) for row in data]
    image = axis.imshow([values], aspect="auto", cmap="RdYlGn")
    axis.set_yticks([0], ["P&L"])
    axis.set_xticks(range(len(sessions)), sessions, rotation=30, ha="right")
    figure.colorbar(image, ax=axis)
    return figure


def strategy_comparison(
    rows: Iterable[Mapping[str, Any]], *, title: str = "Strategy comparison"
) -> Any:
    data = _records(rows)
    figure, axis = _figure(title)
    if not data:
        _empty(axis)
        return figure
    labels = [str(row.get("strategy", "unknown")) for row in data]
    values = [float(row.get("pnl", row.get("value", 0))) for row in data]
    axis.bar(labels, values, color="#1f77b4")
    axis.set_ylabel("value")
    return figure


def spread_by_session(
    rows: Iterable[Mapping[str, Any]], *, title: str = "Spread by session"
) -> Any:
    data = _records(rows)
    figure, axis = _figure(title)
    if not data:
        _empty(axis)
        return figure
    labels = [str(row.get("session", "unknown")) for row in data]
    values = [float(row.get("spread", 0)) for row in data]
    axis.plot(labels, values, marker="o")
    axis.set_ylabel("spread")
    return figure


def save_png(figure: Any, path: str | Path, *, dpi: int = 120) -> Path:
    """Save a figure as a non-interactive PNG and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, format="png", dpi=dpi)
    return target


def save_html(figure: Any, path: str | Path) -> Path:
    """Save a self-contained HTML preview without adding a Plotly dependency."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=120)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    target.write_text(
        '<!doctype html><meta charset="utf-8"><title>Trading chart</title>'
        f'<img alt="Trading chart" src="data:image/png;base64,{encoded}">\n',
        encoding="utf-8",
    )
    return target


__all__ = [
    "VisualizationUnavailable",
    "candlestick",
    "drawdown",
    "equity_curve",
    "save_html",
    "save_png",
    "session_pnl_heatmap",
    "spread_by_session",
    "strategy_comparison",
]

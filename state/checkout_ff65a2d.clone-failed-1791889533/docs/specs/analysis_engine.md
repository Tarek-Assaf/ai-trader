# T-040 Analysis Engine Contract

`build_snapshot(instrument, as_of, minute_candles, spreads=...)` accepts one canonical candle series and timezone-aware `Candle` values. It normalizes timestamps to UTC, validates the input, discards future and incomplete bars, and creates complete UTC-aligned 1M, 3M, and 5M views. No indicator uses data past its output candle.

The engine is pure Python and deterministic. Indicator warm-up windows return `None`. EMA uses an SMA seed; RSI, ATR, and ADX use Wilder-style smoothing; Bollinger deviation uses the population standard deviation; VWAP resets at 00:00 UTC. Sessions are broad UTC tags, not broker opening-hour decisions. Support/resistance values are confirmed swing levels, so a swing is emitted only after its right-side confirmation bars arrive.

`Snapshot.to_json()` emits stable JSON ordering and UTC ISO timestamps. Spread/ATR is populated only when the spread timestamp matches the latest completed 1M candle and ATR is positive. A score is descriptive alignment across available EMA 9/21 directions; it is not an eligibility or trade-quality score.

Daily pivots use the preceding observed UTC calendar day (classic pivot, S1, R1); they do not use the current day's high/low/close. Support and resistance are bounded clusters of already-confirmed swing prices around the latest close.

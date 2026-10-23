# T-221 Smart Filter parity evidence

## Scope and safety

This was a bounded, read-only Pod investigation. No broker session, order, dry-run, lake write, configuration, risk, gate, lease, restart, or secret activity occurred.

## Pod health at 2026-10-23 18:49 UTC

The local `/health` endpoint returned HTTP 200 and reported:

- runtime `running`, mode `paper`, `live_enabled=false`;
- market `TRADEABLE`, `expected_open=true`, stale epics empty;
- recorder `healthy`, `feed_stalled=false`, `minutes_since_last_bar=0.5`;
- `ticks_received=388`, `rejected_candles=2` (reason `ask_close_below_bid_close`), reconnect count 22 (last `ConnectionClosedError`);
- Tai US100: `evaluated=97`, `no_signal=97`, `market_closed_or_break=0`, `stale_or_incomplete=0`, `refused=0`, `entry=0`, `exit=0`, last gate `ha_cross`, mode `forming`.

The feed was advancing and was not stalled, but the recorder had two rejected candles and repeated reconnects. These did not explain the screenshot parity mismatch.

## Target-day slice

Copied only the NAS100 Capital.com parquet rows from 2026-10-21 21:00:00Z through 23:30:00Z (inclusive) into `/tmp/t221-lake`. The filtered slice contained 292 rows.

The deployed `lab_tai_smart_filter.py` was run against this temporary lake with a 120-second bound. It exited 0, but emitted groups only for NSXUSD and XAUUSD; no US100/NAS100 group was produced. The utility therefore cannot supply the requested US100 gate inputs on this Pod image.

## Port replay attempt

The deployed `lab_t155_tai_parity.py` was run against the temporary slice. It could not start because the required owner TradingView CSV is absent:

`docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv`

That file is also for an older NQ1 window, not the owner's US 100 Cash CFD/NAS100 3m screenshot window.

## Verdict

T-221 cannot isolate HTF alignment, VWAP volume, Open/Close versus Renko, or a single gate cause from the current Pod artifacts. The exact missing artifact is a TradingView CSV export for **US 100 Cash CFD / NAS100 3m** covering 2026-10-21 21:00–23:30Z (including the 22:30Z bar), with timestamps and OHLCV. Once supplied, the same-bars comparison can print the HTF, VWAP, ATR and stop inputs side by side. No production fix is proposed or applied from this run.


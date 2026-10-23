# T-221: Same-bars Smart Filter parity (TradingView US100 vs Capital.com lake) - read-only

## Problem
T-219 showed the recorded Capital.com US100 replay and the production Tai provider both give zero
entries on 2026-10-21 (incl. 22:30Z), while the owner's TradingView chart (US 100 Cash CFD, 3m)
shows SELL markers. Unresolved: (a) feed differences, (b) 54-minute HTF (`3m * 18`)
`request.security(..., lookahead_off)` alignment, (c) volume-weighted VWAP (production uses a
zero-volume fallback), (d) Open/Close vs Renko mode.

## Required (read-only; no broker, order, config, risk, gate, dry-run or lease change; no secrets)
1. Copy only the 2026-10-21 NAS100 parquet slices to a temp lake; run the closed port and the
   production `TaiSignalProvider` over 21:00-23:30Z with `--epic NAS100`; list per-bar gate results.
2. For the 22:30Z bar and the owner's screenshot SELL bars, print each gate input (HA values,
   54-min HTF value as seen at that bar, VWAP, ATR, stop) for port and runtime side by side.
3. Isolate which of (a)-(d) flips the SELL gate: re-run with HTF shifted by one 54-min bucket,
   with the closed vs forming HTF bar, and with tick-volume VWAP if the lake has volume.
4. Verdict: name the single cause or state what owner data is needed (e.g. a TradingView CSV
   export of the same window). If a defect is found, propose the fix; do not apply it.
5. Report to `docs/evidence/t221/report.md` plus `summary.json`.

## Acceptance
Read-only, secret-free, ruff (100)/mypy/CI green for any code. One log entry, turn to claude.

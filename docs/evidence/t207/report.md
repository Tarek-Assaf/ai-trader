# T-207 quiet-session check

## Window

Read-only Pod inspection for NSXUSD from **2026-10-20T12:33:22Z** through **2026-10-20T14:34:59Z**. The image supplied the repository under `/workspace/t204_src`; `/app` is not a git checkout.

## Runtime state and trade evidence

- Startup log records `dry_run=off` at 12:33:22Z. Health reports `mode=paper`, `broker_env=demo`, and `live_enabled=false`.
- No entry, refusal, exit, submit, order, or trade event appeared in the runtime log for the window. Postgres `audit_log` returned no rows for the window.
- `/workspace/state/tai_demo_positions.json` remained flat: `positions={}`, `last_exit_at=null`.
- The recorder was degraded by 14:40Z; last closed bar was 14:34Z, last tick 14:35:09Z, with reconnects and rejected candles.

## Closed bars and port replay

The lake contains **117** closed one-minute NSXUSD bars in the window. Resampling produced **40** complete 3-minute bars. The closed replay found **0 candidate crosses**, **0 signals after gates**, and **0 entries**; its gate-failure counts are all zero because no candidate existed. Port outcomes are therefore **40 no_signal**, **0** market-closed/break, **0** stale, **0** incomplete, **0** refused, and **0** entries.

Runtime-versus-port comparison is **matched 0, extras 0, misses 0** (both entry sets are empty).

## Blocker

The runtime log exposes **107** closed-minute lines versus **117** closed-minute lake bars, a **10-bar feed gap**. Runtime logs and audit rows expose no per-bar outcome counters, so the runtime evaluated-bar count and runtime gate-failure split cannot be independently verified. This is the first differing quantity and remains the T-207 blocker.

No configuration, code, risk/gate, restart, broker, order, or secret operation was performed.

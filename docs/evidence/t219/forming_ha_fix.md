# T-219 forming higher-timeframe Heikin-Ashi fix

## Cause

The production Tai provider always exposed the last confirmed 54-minute
Heikin-Ashi value to every 3-minute bar in the current group. The Smart Filter
script uses `request.security` in realtime, where the current higher-timeframe
value is still developing. At 18:30 New York (22:30Z), that difference can
prevent the `ha_cross` SELL from appearing until the 18:54 group close.

## Change

`TaiSignalProvider` now accepts `ha_mode=\"closed\"|\"forming\"`. `closed`
preserves deterministic backtest behavior. `forming` computes the current
54-minute OHLC prefix for each closed 3-minute bar, while advancing the
confirmed Heikin-Ashi state only after a complete group.

The opt-in Tai runtime reads `TAI_DEMO_HA_MODE`, defaulting to `forming` when
the already-gated Tai experiment is enabled. `TAI_DEMO_DRY_RUN` and the
paper/demo/live safety gates remain unchanged. The selected mode is included in
the warmup journal and health snapshot for diagnosis.

## Validation

- Focused Tai/settings/demo tests: 42 passed.
- Forming-mode regression: a SELL can appear inside the current 54-minute
  group; closed mode does not expose it until the group is complete.
- Ruff check and format check: pass.
- Python compileall: pass.
- Full suite and CI quality remain required before merge. No Pod replay was
  attempted in this branch because the owner-provided endpoint currently fails
  TLS negotiation from the desktop.
- No broker session, order, dry-run order, credential, or live behavior was
  used during validation.

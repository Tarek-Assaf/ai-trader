# T-207: Why no Tai entries so far? (owner demo session 2026-10-20)

## Why
Demo orders were enabled at 12:33Z. At ~15:08Z the Pod shows no Tai entry, refusal, exit or order, flat state (T-206 plus owner
check). Entries average a few per day in the port replay, so a quiet 2.5 hours may be normal, but we must prove the agent is
evaluating bars and would agree with the port on the same bars.

## Do (read-only on the Pod; no config, code, order or restart)
1. Count the closed 3m NSXUSD bars the runner evaluated since 12:33:22Z, split by outcome (no_signal, market closed/break, stale,
   incomplete, refused, entry). Report the gate that failed most often (ha_cross, ema200, ADX/DI, VWAP distance, swing/risk).
2. Run the closed port (`scripts/lab_t162_pine_port.py`, same 3m bars from the live recorder or lake, same timestamps) over the
   same window. Report port entries in the window (matched / extra / missing vs the runtime). Zero on both sides is a pass.
3. Report the daily entry rate in the port for the last 10 trading days to show what a normal count looks like.
4. If the runtime missed a port entry or evaluated fewer bars than expected (feed gaps, stale flags), name the first differing
   quantity with numbers and raise a blocker.

## Rules
Read-only. Dry-run flag, risk/gate files, strategy code untouched. No secrets in output.

## Acceptance
`docs/evidence/t207/report.md` + `summary.json`; one log entry; turn to claude; stop.

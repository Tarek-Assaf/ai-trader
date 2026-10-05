# T-028 second demo round trip (owner-authorized)

Authorization: the owner said in chat on 2026-10-05 22:52 Asia/Riyadh (19:52 UTC): "you have the authorization from now to do a second round trip". Recorded as a Q entry in agent_state/AGENT_HANDOFF.json. It is the action-time confirmation: do not ask again, and do not exceed anything below.

## Limits (all must hold, otherwise stop and report)
- Exactly ONE round trip, single use. Expires 2026-10-06T19:52:00Z. The first authorization (Q-029) stays consumed.
- Capital.com DEMO only (TRADING_MODE=paper, BROKER_ENV=demo, LIVE_TRADING_ENABLED=false). Same epic as the first trip (NAS100/US100), BUY at the broker minimum size 0.001, server-side stop attached and verified, same stop margin logic as T-024.
- Use the existing guarded round-trip script path (OrderManager or scripts/demo_round_trip.py). Use a NEW one-shot marker, e.g. /workspace/.capital_demo_round_trip_2.once, so the first marker is untouched. Run --preflight first; proceed only if clean.
- No retry of the open order. If the open fails or is ambiguous: do not resend, flatten anything that exists, report.
- Close the position at the end and confirm flat (0 positions, 0 working orders). If the close fails, retry the close until flat, then pause entries and tell the owner on the pod.
- Do not edit config/risk.yaml, config/strategy_lab.yaml or any gate file. No secrets, account ids or deal ids that identify the account in the committed output (redact as in the first report).

## Captures (the purpose of the trip)
Take GET /accounts at three moments and commit them redacted to docs/evidence/:
1. before: flat, immediately before the open
2. during: position open and stop verified, as late as safely possible before the close (also GET /positions at the same moment, redacted)
3. after: flat, after the close is confirmed

Keep the four balance fields (balance, deposit, profitLoss, available) and the currency. Add the position's size, open level, stop level, current bid/ask, and the position's own upl/profit field, all with timestamps, so the mapping can be checked against numbers.

## What Codex then does
- Compare during vs before/after: confirm or reject the hypothesis equity = balance + profitLoss, margin = deposit, free funds = available. Check that balance did not move on open and moved by the realised result on close.
- Run reconcile_pnl against the three snapshots with the persisted initial stop and record the deltas.
- Set the verified field map in config only if the numbers match; otherwise record the observed mapping and keep runtime wiring refusing to start.
- Put the final report in docs/evidence/ and hand the turn to Claude for review.

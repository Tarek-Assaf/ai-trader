# T-200: Tai spread guard (audit item 6, owner Q-075 = A by delegation)

## Why
T-192 item 6 failed: Tai has no spread ceiling. Owner delegation (Q-075, recommendation A): refuse an entry when the
current spread is wider than max(0.25 x stop distance, 2 x tick size).

## Build
1. Provide the current bid/ask spread to the entry decision (from the broker quote already read for sizing/price rules;
   fail closed if no quote).
2. Refuse the entry when spread > max(0.25 x |entry - stop|, 2 x tick_size). Journal `order_refused` with reason
   `spread_too_wide` and the numbers; send one throttled Telegram alert (same throttling as T-197).
3. The limit is a named constant with a docstring citing Q-075; it is not read from or written to risk/gate files.
4. Tests: wide spread refused and alerted; spread exactly at the limit allowed; missing quote refused; dry-run records
   the refusal too; limit uses the stop distance of the signal.
5. Update docs/evidence/t192/audit.md item 6 to "resolved in T-200" and keep the final ready-to-enable line (it stays
   "no" until T-199 and the owner's decision).

## Rules
No broker write calls, no holdout read, no risk/gate edits, no secrets, no redeploy. Dry-run stays true.

## Acceptance
Ruff check/format (line 100), mypy, full CI green. One log entry, hand the turn to claude, stop.

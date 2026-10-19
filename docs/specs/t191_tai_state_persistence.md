# T-191: Persist Tai demo position state and reconcile at startup (required before real demo orders)

## Why
T-189 keeps managed positions, queued bars and the 3m builder across an in-process runtime rebuild only.
`TaiDemoRunner._managed` is in memory. If the API process or Pod restarts while a demo position is open,
the runner forgets the 40/30/30 plan and the break-even stop move, and the open position is left without
exit management.

## Build
1. Persist `_managed` after every change (entry, each partial exit, stop moved) to a small JSON file under the
   workspace state dir (atomic write: temp file + rename; keyed by deal id; no secrets; includes epic,
   direction, entry, risk, targets, original size, hit set, size step, one_r_value).
2. At startup with the experiment on: load the file, call `get_positions()` once, and reconcile: a stored deal
   that is gone at the broker is dropped and journaled `reconcile_closed`; a broker position of the Tai epic
   with no stored plan is journaled `reconcile_unmanaged` and Telegram-alerted, and the runner refuses new
   entries while it exists (fail closed; no automatic management of unknown positions).
3. Dry-run: keep a shadow position record in the same file (marked shadow) so a restart in dry-run neither
   loses it nor double-enters; the shadow position follows the same exit plan using bars (no broker call).
4. Telegram alert on every entry, partial exit, stop move and flat, in dry-run too (prefixed "DRY-RUN").

## Tests
Restart with open position restores management; broker position missing -> dropped; unknown broker position ->
refuse entries and alert; corrupt file -> fail closed with an alert, no entry. Dry-run shadow survives restart.

## Rules
Demo only, dry-run default stays true. No risk/gate edits, no secrets in the file or logs. CI green (ruff line
100, mypy, pytest). Hand back to claude; Claude gives the owner the redeploy notice.

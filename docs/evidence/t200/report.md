# T-200 offline evidence

Implementation completed from authoritative `origin/main` for Q-075 = A. The Tai entry path now carries the broker snapshot bid/offer with instrument rules and refuses entries when:

`ask - bid > max(0.25 * abs(entry - stop), 2 * tick_size)`

The exact limit is allowed. Missing bid, offer, or tick size refuses closed with `spread_quote_unavailable`. Refusals journal `order_refused` with the quote, spread, stop distance, tick size, and limit, and send one throttled alert per epic and reason. The path runs before order construction and applies to dry-run decisions as well.

Validation was offline only: Ruff format/check, Python compilation, and `git diff --check` passed. The desktop has no `pytest` module, so the focused unit suite and full CI remain for review. No broker calls, orders, holdout reads, redeploy, risk or gate edits, or secrets were used.

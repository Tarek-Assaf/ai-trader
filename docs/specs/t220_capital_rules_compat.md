# T-220: Capital.com US100 rule-shape compatibility

## Problem

The Capital.com demo `GET /markets/US100` response does not include
`instrument.tickSize` or `instrument.valuePerPoint`.  It returns the tick in
the exact market-search result, uses `instrument.lotSize` for the contract
value, and names the protective distance `dealingRules.minStopOrProfitDistance`.
That distance is commonly expressed as a percentage of the current quote.

The Tai runner previously required the other field names and rejected a valid
signal as `order_rules_unavailable` before an order request was built.

## Acceptance criteria

- Parse the live Capital.com field names without hard-coded US100 values.
- Enrich a missing tick from the exact market-search result; derive the tick
  from `decimalPlacesFactor` only when the response itself contains it.
- Use `lotSize` as the value-per-point fallback when the optional field is
  absent.
- Convert point and percentage stop/limit distances into absolute price
  distances using the broker tick and current quote.
- Log the specific redacted refusal reason while preserving the existing
  fail-closed behavior.
- Add focused tests for the actual US100 response shape and provider
  enrichment. No broker writes, order placement, risk or gate changes.


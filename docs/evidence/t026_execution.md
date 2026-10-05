# T-026 execution boundary

The order manager reserves an immutable order under `cycle_id` in PostgreSQL's
existing `trade_intents` table before submission. The database primary key
arbitrates duplicate requests across processes. Reservations are never deleted
or retried, including an accepted order whose response was lost. The initial
stop is retained independently of subsequent broker stop modifications.

Execution and Guardian each require a separate Redis token lease. Renewal and
release compare ownership atomically. Every entry reconciles first, checks P&L,
and evaluates Guardian approval bound to the exact order under the Guardian
lease. Reconciliation mismatches latch a durable pause and notify through the
injected alert channel. A service loop must call `tick()`; startup reconciles
before permitting entries. Explicit `resume()` requires a clean reconciliation.

An ambiguous reservation remains paused for operator investigation. Positions
and activity are inspected, but absence of a position is never treated as
permission to resend. An unknown broker position is retained in the read model
and pauses entries. No automatic close is attempted by reconciliation.

## Account normalization limitation

[Capital.com's public API](https://open-api.capital.com/) lists `balance`,
`deposit`, `profitLoss`, and `available` in account responses, without defining
their accounting semantics in that schema. The normalizer therefore requires an
explicit active account ID and a validated cash/equity field mapping. Margin and
account-currency exposure are explicit inputs from validated valuation.
No zero or guessed field defaults are used. The mapping used in offline tests
is a fixture, not evidence about the demo account.

The Q-029 Pod round trip and Q-031 demo-account comparison remain pending. On
the Pod, validate cash/equity semantics and position margin/exposure valuation,
then call `reconcile_pnl()` using persisted trade valuation and initial stops.
Any missing input, non-positive equity, or reconciliation exception must pause
entries and alert. No demo or live broker calls were made for this implementation.

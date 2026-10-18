# T-151 owner trades comparison

Diagnostic only. Owner timestamps are interpreted as UTC; the sealed holdout, broker, gates, costs and ledger were not read.

| Variant | Anchor | Signals | Warm-up excluded | ±1 | ±2 | ±5 | Extra at ±5 |
|---|---|---:|---:|---:|---:|---:|---:|
| closed | cme_open | 3 | 0 | 0/31 | 0/31 | 0/31 | 3 |
| closed | utc_midnight | 0 | 1 | 0/31 | 0/31 | 0/31 | 0 |
| closed | riyadh_midnight | 1 | 1 | 0/31 | 0/31 | 0/31 | 1 |
| forming_reset | cme_open | 9 | 4 | 0/31 | 0/31 | 0/31 | 9 |
| forming_reset | utc_midnight | 12 | 5 | 0/31 | 0/31 | 0/31 | 12 |
| forming_reset | riyadh_midnight | 6 | 2 | 0/31 | 0/31 | 0/31 | 6 |
| forming_carried | cme_open | 7 | 4 | 0/31 | 0/31 | 0/31 | 7 |
| forming_carried | utc_midnight | 10 | 7 | 0/31 | 0/31 | 0/31 | 10 |
| forming_carried | riyadh_midnight | 5 | 2 | 0/31 | 0/31 | 0/31 | 5 |

Trial count: 9 (three signal variants x three session anchors).

The forming-candle hypothesis is supported only when a forming variant materially increases same-direction owner-entry hits without parameter tuning; the table reports every pre-registered anchor and variant.

Price basis: {'matched_entries': 27, 'missing_entry_minutes': 4, 'owner_minus_nas100_mid_points': {'median': 209.15000000000146, 'min': -17.05000000000291, 'max': 385.40000000000146}}

Safety: broker calls 0, orders 0, sealed holdout read false, and no gate, cost, ledger or promotion writes.

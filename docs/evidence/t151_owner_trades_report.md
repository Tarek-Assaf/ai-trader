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
| refresh | cme_open | 31 | 0 | 0/31 | 0/31 | 1/31 | 30 |
| refresh | utc_midnight | 31 | 0 | 0/31 | 0/31 | 1/31 | 30 |
| refresh | riyadh_midnight | 31 | 0 | 0/31 | 0/31 | 1/31 | 30 |
| refresh_gated | cme_open | 29 | 0 | 0/31 | 0/31 | 1/31 | 28 |
| refresh_gated | utc_midnight | 29 | 0 | 0/31 | 0/31 | 1/31 | 28 |
| refresh_gated | riyadh_midnight | 29 | 0 | 0/31 | 0/31 | 1/31 | 28 |

Trial count: 15 (5 variants x three session anchors).

The forming-candle hypothesis is supported only when a forming variant materially increases same-direction owner-entry hits without parameter tuning; the table reports every pre-registered anchor and variant.

Price basis: {'matched_entries': 27, 'missing_entry_minutes': 4, 'owner_minus_nas100_mid_points': {'median': 209.15000000000146, 'min': -17.05000000000291, 'max': 385.40000000000146}}

## Refresh path and per-entry conditions

Variant `refresh` emits a filter-and-risk refresh every 12 flat bars without virtual in-trade gating; `refresh_gated` keeps a virtual trade open until its stop or 3R target. The condition table uses the UTC-midnight 54m anchor and evaluates all 31 owner entries without parameter tuning.

Condition pass counts: {'ha_state': 7, 'ema_side': 15, 'adx_di': 6, 'vwap_side': 13, 'dont_chase': 7, 'risk_atr': 18}
Condition failure counts: {'ha_state': 20, 'ema_side': 12, 'adx_di': 21, 'vwap_side': 14, 'dont_chase': 20, 'risk_atr': 9}
All filters passed: 0/31.
Stop fit: {'entries_with_owner_stop': 11, 'median_raw_error_points': 19.084480038061884, 'median_basis_adjusted_error_points': 24.100388385533734}

| Entry | Time | Direction | HA | EMA | ADX/DI | VWAP | Chase | Risk | Failed conditions |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-06 16:03:00+00:00 | short | None | None | None | None | None | None | none |
| 2 | 2026-10-06 18:45:00+00:00 | short | short | False | True | False | False | True | ema_side, vwap_side, dont_chase |
| 3 | 2026-10-06 21:18:00+00:00 | short | None | False | False | False | False | True | ha_state, ema_side, adx_di, vwap_side, dont_chase |
| 4 | 2026-10-07 18:21:00+00:00 | long | None | False | False | False | False | True | ha_state, ema_side, adx_di, vwap_side, dont_chase |
| 5 | 2026-10-07 21:51:00+00:00 | long | None | False | True | False | False | False | ha_state, ema_side, vwap_side, dont_chase, risk_atr |
| 6 | 2026-10-08 10:45:00+00:00 | long | short | False | False | False | False | True | ha_state, ema_side, adx_di, vwap_side, dont_chase |
| 7 | 2026-10-08 11:09:00+00:00 | short | None | True | True | True | False | True | ha_state, dont_chase |
| 8 | 2026-10-08 19:03:00+00:00 | short | short | True | False | True | False | True | adx_di, dont_chase |
| 9 | 2026-10-09 10:30:00+00:00 | short | short | False | False | False | False | False | ema_side, adx_di, vwap_side, dont_chase, risk_atr |
| 10 | 2026-10-09 12:30:00+00:00 | long | None | True | False | True | False | False | ha_state, adx_di, dont_chase, risk_atr |
| 11 | 2026-10-11 18:18:00+00:00 | long | None | None | None | None | None | None | none |
| 12 | 2026-10-11 20:27:00+00:00 | long | None | None | None | None | None | None | none |
| 13 | 2026-10-11 22:30:00+00:00 | long | None | None | None | None | None | None | none |
| 14 | 2026-10-13 07:00:00+00:00 | short | long | True | False | True | True | True | ha_state, adx_di |
| 15 | 2026-10-13 10:57:00+00:00 | short | None | True | True | False | False | True | ha_state, vwap_side, dont_chase |
| 16 | 2026-10-13 13:36:00+00:00 | short | long | False | False | False | False | False | ha_state, ema_side, adx_di, vwap_side, dont_chase, risk_atr |
| 17 | 2026-10-13 14:21:00+00:00 | long | long | True | False | True | False | True | adx_di, dont_chase |
| 18 | 2026-10-14 01:24:00+00:00 | short | long | False | False | False | True | False | ha_state, ema_side, adx_di, vwap_side, risk_atr |
| 19 | 2026-10-14 04:54:00+00:00 | short | long | False | False | False | False | True | ha_state, ema_side, adx_di, vwap_side, dont_chase |
| 20 | 2026-10-14 06:18:00+00:00 | short | short | True | True | True | False | True | dont_chase |
| 21 | 2026-10-14 08:18:00+00:00 | long | long | True | False | True | True | False | adx_di, risk_atr |
| 22 | 2026-10-14 18:24:00+00:00 | short | None | True | True | True | False | True | ha_state, dont_chase |
| 23 | 2026-10-15 00:30:00+00:00 | short | None | True | False | False | True | True | ha_state, adx_di, vwap_side |
| 24 | 2026-10-15 01:27:00+00:00 | short | None | True | False | True | True | False | ha_state, adx_di, risk_atr |
| 25 | 2026-10-15 08:45:00+00:00 | short | long | True | False | True | False | True | ha_state, adx_di, dont_chase |
| 26 | 2026-10-15 09:42:00+00:00 | short | long | True | False | True | True | True | ha_state, adx_di |
| 27 | 2026-10-15 20:57:00+00:00 | long | None | True | False | True | False | False | ha_state, adx_di, dont_chase, risk_atr |
| 28 | 2026-10-16 06:12:00+00:00 | short | long | False | False | False | False | False | ha_state, ema_side, adx_di, vwap_side, dont_chase, risk_atr |
| 29 | 2026-10-16 07:03:00+00:00 | short | short | False | False | False | False | True | ema_side, adx_di, vwap_side, dont_chase |
| 30 | 2026-10-16 09:18:00+00:00 | short | long | False | False | False | False | True | ha_state, ema_side, adx_di, vwap_side, dont_chase |
| 31 | 2026-10-16 10:42:00+00:00 | short | None | True | False | True | True | True | ha_state, adx_di |

Safety: broker calls 0, orders 0, sealed holdout read false, and no gate, cost, ledger or promotion writes.

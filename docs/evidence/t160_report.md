# T-160 owner-time replay

## Owner verdict

**STOP — New York conversion fixes the time alignment, but the T-157 rule replay still fails.** The shared owner-CSV reader now parses America/New_York with zoneinfo; the T-159 rerun selects a **0-minute** best shift with 49 comparable pairs and 87.76% directional agreement. The required T-157 baseline replay remains **1/31 center-bar passes** and **2/31 entries passing within ±3 bars**. The first failing rule is ha_state for 19 entries. No variants, parameter tuning, broker calls, orders, holdout reads, or production writes were used.

The result means the timezone mismatch is resolved, while the remaining TradingView/replay mismatch is in rule/data behavior. Production timestamps remain UTC.

## Time conversion

- 2026-10-06 10:00 New York -> 2026-10-06T14:00:00+00:00.
- 2026-12-01 10:00 New York -> 2026-12-01T15:00:00+00:00.
- Ambiguous 2026-11-01 01:30 uses fold=0 (earlier occurrence) -> 2026-11-01T05:30:00+00:00.
- Nonexistent DST spring-forward local times are rejected by round-trip validation.

## T-159 alignment rerun

| Best shift | Comparable | Agreement | Correlation | Permutation p |
| ---: | ---: | ---: | ---: | ---: |
| 0 minutes | 49 | 87.76% | 0.7543 | 0.0004997501 |

## T-157 baseline replay

| Metric | Result |
| --- | ---: |
| Center-bar all-pass | 1/31 |
| Any pass within ±3 bars | 2/31 |
| First failure: ha_state | 19 |
| ema_side | 3 |
| adx_di | 2 |
| vwap_side | 1 |
| dont_chase | 4 |
| risk_atr | 0 |
| pass | 1 |
| missing_bar | 1 |

The Pod replay produced 5,670 rows across all bases/variants/offsets. docs/evidence/t160_replay.csv is a 31-row compact center-bar index; the complete run is identified by SHA-256 635db718e01bbf79c4dc06789462c74e6b6b890cff054394430001ae77a755c7. The full Pod report hash is 6f7f0113738dd22bf5c7879a9073f7fefd3580d60f74489065986d72ddccf5db.

## Safety

Safety evidence: broker_calls=0, orders=0, sealed_holdout_read=false, lake_writes=0, production_state_writes=0.

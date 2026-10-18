# T-159 Time alignment

## Owner verdict

**STOP_NO_SAFE_CORRECTION.** A +240-minute shift is statistically repeatable (49 comparable
trades, 87.76% directional agreement, correlation 0.7543, 2,000-permutation p=0.0005), but
the evidence does not identify a safe faulty layer. The owner export is explicitly UTC, while
Capital.com parsing, recorder normalization, and UTC resampling are explicit UTC contracts.
Although +240 minutes is America/New_York daylight time during this window, applying that
timezone would contradict the owner contract. No production correction was made and T-157 was
not rerun because the required correction prerequisite was not met. The historical source
timestamp must be confirmed with the owner/producer before any code change.

No broker calls, orders, sealed holdout reads, lake writes, production-state writes, strategy
tuning, or promotion activity occurred.

## Stage 1 — independent offset confirmation

The 5-minute grid from -6h to +6h used all paired entry/exit rows (54 tranches). Zero shift
matched 43 pairs at 46.51% agreement with -0.1106 correlation. The best result was +240m with
49 pairs at 87.76% agreement and 0.7543 correlation. A deterministic 2,000-permutation
max-over-grid test gave p=0.0004997501; the offset is therefore repeatable, not a random
single-grid winner.

## Stage 2 — session and gap check

The lake contains mostly one-minute steps but also irregular 13–96 minute recorder gaps and a
2,975-minute weekend gap. These do not form a clean CME break/DST translation, so the lake
schedule does not justify shifting timestamps. The observed missing periods are consistent with
recording/connectivity gaps.

## Stage 3 — locate the layer

- `docs/specs/t151_owner_trades_comparison.md:3,14`: owner confirmed export times are UTC.
- `src/trader/integrations/capital_com/stream.py:243,257-273`: stream and REST timestamps parse as UTC.
- `src/trader/data/recorder.py:60,259,289,299`: aware timestamps normalize to UTC and truncate to minute.
- `src/trader/data/resample.py:53-68`: bars use UTC bucket starts.

The source contracts are internally consistent. The +240 result is a diagnostic mismatch
between the historical owner replay and recorded data, not a proven production parser defect.

## Acceptance outcome

The repeatability requirement passes. The locate-layer requirement finds no safe correction
layer, so the one-correction limit prevents a timezone guess. The T-157 corrected replay is
intentionally not run. The exact blocker handed to Claude is: **confirm whether the historical
owner export was actually generated in UTC or America/New_York EDT; the repository contract and
the statistical offset disagree.**

## Safety

```json
{"broker_calls": 0, "orders": 0, "sealed_holdout_read": false, "lake_writes": 0, "production_state_writes": 0}
```

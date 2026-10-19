# T-166 Pine-port conformance

**Verdict:** The diagnostic Open/Close port matches the stored Pine source on **12 of 15 audited trading rules; 0 differences fixed; 0 unknown inputs remain after the owner's Inputs screenshot**. Two source branches (Renko and the original ATR/RSI sideways filter) are intentionally not implemented, and the date filter is only represented by the comparison window rather than by Pine's per-bar `dateAllowed` predicate. No port change was justified by a failing known-answer test.

This is a data-independent audit of the stored source at `docs/evidence/owner/tai_smart_filter_pine_source.txt`. It does not claim that the port reproduces the owner's chart bars. The owner-provided Inputs screenshots (Q-066) confirm the source defaults used for this audit. The chart-bar CSV is still needed for signal reproduction, but not for this rule audit.

## Rule-by-rule audit

| Pine source lines | Port function/line | Known-answer test | Status |
| --- | --- | --- | --- |
| 21-36, 194-214: resolution-to-minutes and `tfmult` timeframe | `TIMEFRAME`, `TF_MULT`, `HIGHER_MINUTES` lines 40-42; `run` lines 654-664 | `test_t166_scope_records_unsupported_setup_and_source_defaults` | covered |
| 46-50, 220-248, 318-330: Open/Close HA crossover entry signals | `_ha_series` 228-311; `_crosses` 314-325; `replay` 376-398 | `test_crossover_and_crossunder` | covered |
| 254-312: Renko request/security and Renko EMA branch | no implementation; `replay` only consumes Open/Close crosses | `test_t166_scope_records_unsupported_setup_and_source_defaults` | missing |
| 95-188: RSI and original ATR/RSI sideways filter | no RSI/original-filter function; `compute_features` starts at line 328 | `test_t166_scope_records_unsupported_setup_and_source_defaults` | missing |
| 72-89, 602-616: `useDateFilter`, `fromDate`, `toDate`, and per-bar `dateAllowed` | `START_DATE` line 49 and owner scan window `run` lines 654-663; no per-bar signal predicate | `test_t166_scope_records_unsupported_setup_and_source_defaults` | differs |
| 220-234: `request.security` Heikin-Ashi values with `lookahead_off` | `_ha_series` lines 228-311 | `test_t166_closed_ha_values_are_lookahead_off` | covered |
| 342-356: EMA 200 trend direction | `_ema` lines 109-119; `_smart_ok` lines 352-373 | `test_t166_smart_filter_requires_direction_and_location` | covered |
| 362-397: DMI/ADX Wilder smoothing and directional gate | `_dmi` lines 138-164; `_smart_ok` lines 352-373 | `test_rma_seeds_at_period_and_wilders` | covered |
| 403-415: session VWAP direction | `_vwap` lines 167-191; `_smart_ok` lines 352-373 | `test_t166_vwap_resets_each_chart_day_and_uses_typical_price_for_zero_volume` | covered |
| 421-442: ATR-normalized distance from VWAP | `_smart_ok` lines 352-373 | `test_t166_smart_filter_requires_direction_and_location` | covered |
| 448-458: final long/short smart-filter conjunction | `_smart_ok` lines 352-373 | `test_t166_smart_filter_requires_direction_and_location` | covered |
| 466-554: previous swing, ATR buffer, risk cap | `_risk` lines 338-349 | `test_t166_structure_risk_uses_previous_bars_and_atr_buffer` | covered |
| 562-596: refresh entries, `lastExitBar`, and no HA gate | `replay` lines 376-398, 466-467 | `test_refresh_entry_does_not_require_ha_cross` | covered |
| 753-864: entry at close, structure stop, TP1/TP2/TP3 at 1/2/3R | `replay` lines 399-412 | `test_entry_bar_target_first_and_tp3` | covered |
| 931-1305: entry-bar management, target-first ordering, BE after TP1/TP2 | `replay` lines 413-465 | `test_same_bar_target_wins_over_stop`, `test_tp1_then_break_even` | covered |
| 1307-1315: chart-bar exit tracking for refresh timing | `replay` lines 466-467 | `test_refresh_entry_does_not_require_ha_cross` | covered |

### Missing/different rule tests

The three non-covered rules are recorded as explicit scope tests rather than silently guessed. The tests assert the public Open/Close contract and date/input dependency; they do not claim Renko or original-filter parity. No source rule was changed because no failing known-answer test identified a defect in an implemented rule.

## Inputs and defaults

The Pine source declares 34 inputs. The port uses the following source defaults as constants or fixed behavior:

- `setupType="Open/Close"` (lines 46-50); `tfmult=18` (62-66); `fromDate=01 Jan 2023 +0300` (77-80).
- `useEMA200=true` and EMA length 200 (342-350); `useADX=true`, `adxLength=14`, `adxSmoothing=14`, `minimumADX=20.0` (362-389).
- `useVWAPDirection=true` and VWAP `hlc3` (403-415); `useLocationFilter=true`, `maxVWAPDistance=1.5` (421-442).
- `useStructureSL=true`, `swingLookback=5`, `structureBufferATR=0.20`, `maxRiskATR=2.0`; the Pine `atrStopMultiplier=1.0` fallback is not selectable while structure stops are hard-coded on (466-554).
- `useRefreshEntries=true`, `refreshBars=12` (562-596).
- The port's fixed chart timeframe is 3 minutes and the fixed higher-timeframe group is 54 minutes (`TIMEFRAME=3`, `TF_MULT=18`, `HIGHER_MINUTES=54`).

Q-066 records the owner's Inputs screenshots: every selected value matches these source defaults (including the displayed New York conversion of the +0300 date defaults). Therefore no owner-input value is unknown for this audit. The complete source input/default list is:

- Signal settings: `setupType` (Open/Close or Renko), `showBars` (true), `showRibbon` (false), `tfmult` (18).
- Date filter: `useDateFilter` (true), `fromDate` (01 Jan 2023 +0300), `toDate` (31 Dec 2099 +0300).
- Original filter: `rsiLength` (7), `topRSI` (45), `bottomRSI` (10), `typeFilter` (No Filtering).
- Renko settings: `renkoEMA1Length` (2), `renkoEMA2Length` (10).
- Smart entry filters: `useEMA200` (true), `useADX` (true), `adxLength` (14), `adxSmoothing` (14), `minimumADX` (20.0), `useVWAPDirection` (true), `useLocationFilter` (true), `maxVWAPDistance` (1.5).
- Structure risk: `useStructureSL` (true), `swingLookback` (5), `structureBufferATR` (0.20), `atrStopMultiplier` (1.0), `maxRiskATR` (2.0).
- Refresh: `useRefreshEntries` (true), `refreshBars` (12).
- Visual/trade display: `showEMA200` (false), `showVWAP` (false), `showLevels` (true), `showEntryLabels` (true), `showResultLabels` (true), `showDashboard` (true). The two source input declarations around lines 622-632 are `showEMA200` and `showVWAP`; no trading rule depends on their display-only values.

The owner-input dependency is resolved by Q-066. The audit does not infer settings from the 31-trade export and does not change the port public contract.

## Safety

No broker calls, orders, holdout reads, lake writes, production-state writes, tuning, or strategy search were performed. The deliverables are this audit and hand-made unit tests only.


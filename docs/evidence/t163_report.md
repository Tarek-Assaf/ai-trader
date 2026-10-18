# T-163 filter/anchor diagnosis

**Verdict:** No registered anchor/filter change reaches the 25/31 reproduction bar. The best VWAP variant (ny_18:00_equal) passes 9/29 all four filters, and the best 54m HA anchors (ny_18:00 and ny_00:00) reproduce 5 entries within +/-2 three-minute bars. The owner needs to provide the Pine Inputs/Style settings for VWAP session/weighting and the higher-timeframe HA anchor.

This was one bounded offline Pod replay. No tuning, historical rerun, broker call, sealed-holdout read or lake write was performed.

## VWAP variants

| Variant | Rows with bar | VWAP side | VWAP distance | All four |
| --- | ---: | ---: | ---: | ---: |
| utc_midnight_equal | 29 | 18 | 13 | 4 |
| ny_18:00_equal | 29 | 23 | 21 | 9 |
| ny_09:30_equal | 29 | 21 | 14 | 4 |
| ny_00:00_equal | 29 | 20 | 9 | 4 |
| utc_midnight_volume | 29 | 18 | 13 | 4 |
| tick_count_unavailable | 0 | 0 | 0 | 0 |

## 54m HA anchors

| Anchor | Port entries | Within +/-2 bars |
| --- | ---: | ---: |
| utc_midnight | 0 | 0 |
| ny_18:00 | 30 | 5 |
| ny_00:00 | 30 | 5 |
| ny_09:30 | 26 | 4 |
| day_first | 26 | 4 |

## Filter subsets

All 15 non-empty subsets of EMA200 side, ADX/DI, VWAP side and VWAP distance are recorded in t163_variants.json. The best subset is EMA alone at 24/29 owner rows; none reaches 28/30 or 25/31.

## ATR risk and coverage

ATR risk passes: 19/29 rows with a recorded 3m bar. NSXUSD coverage: 12,350 1m, 4,013 3m, 2,355 5m and 118 60m bars. XAUUSD has no owner rows. Recorder bars carry volume: False. Each 54m group uses 18 three-minute bars.

## Safety

~~~json
{"broker_calls":0,"orders":0,"sealed_holdout_read":false,"lake_writes":0,"production_state_writes":0,"gate_evaluated":false}
~~~

## Owner action

Please provide a screenshot of the Pine indicator Inputs and Style tabs, including VWAP session/anchor, weighting/source and higher-timeframe Heikin Ashi settings.

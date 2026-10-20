# T-194 Tai port history across all timeframes

**Verdict:** Diagnostic only; no Gate 1 or promotion decision. The unchanged T-162 port ran on full TRAINING history for 1m, 3m, 5m and 60m for NSXUSD and XAUUSD. Configured costs are negative across every non-empty cell; zero-cost values are a counterfactual diagnostic.

Window: `2020-10-17T00:00:00` through `2026-10-17T00:00:00` (end exclusive).
Label: `port of the stored indicator, loosely matched to the owner`.

## Overall results

| Instrument | TF | Trades | Win rate | Net R | PF | Max DD R | Zero-cost net R | Zero-cost PF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NSXUSD | 1m | 10694 | 0.227 | -27464.087 | 0.121 | 27464.311 | 2951 | 1.681 |
| NSXUSD | 3m | 5751 | 0.238 | -9490.896 | 0.210 | 9506.001 | 1898 | 1.860 |
| NSXUSD | 5m | 4206 | 0.225 | -3678.098 | 0.338 | 3700.873 | 1216 | 1.751 |
| NSXUSD | 60m | 0 | — | 0 | — | 0 | 0 | — |
| XAUUSD | 1m | 11144 | 0.246 | -104504.210 | 0.017 | 104508.934 | 3965 | 1.934 |
| XAUUSD | 3m | 6296 | 0.236 | -22223.481 | 0.066 | 22233.118 | 2078 | 1.873 |
| XAUUSD | 5m | 4542 | 0.234 | -14243.550 | 0.084 | 14243.550 | 1447 | 1.831 |
| XAUUSD | 60m | 0 | — | 0 | — | 0 | 0 | — |

The 60m cells produced zero signals/trades and are reported as empty results. Each run used seven days of warm-up. Per-session splits use broad UTC tags and overlap by design during London/New York overlap; they are descriptive, not broker schedule claims.

## Per-year splits (configured net R / zero-cost net R; trades in parentheses)


### NSXUSD 1m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| 2020 | -2577.428 (414) | 109 (414) |
| 2021 | -7595.078 (1830) | 606 (1830) |
| 2022 | -2839.993 (1917) | 477 (1917) |
| 2023 | -3737.251 (1644) | 405 (1644) |
| 2024 | -4086.933 (1905) | 505 (1905) |
| 2025 | -4760.863 (1706) | 537 (1706) |
| 2026 | -1866.542 (1278) | 312 (1278) |

### NSXUSD 3m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| 2020 | -208.534 (234) | 82 (234) |
| 2021 | -1298.456 (994) | 345 (994) |
| 2022 | -1297.891 (1025) | 280 (1025) |
| 2023 | -3691.221 (887) | 294 (887) |
| 2024 | -2015.800 (958) | 363 (958) |
| 2025 | -748.770 (952) | 313 (952) |
| 2026 | -230.224 (701) | 221 (701) |

### NSXUSD 5m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| 2020 | -110.534 (155) | 31 (155) |
| 2021 | -836.516 (745) | 229 (745) |
| 2022 | -1216.404 (695) | 156 (695) |
| 2023 | -634.989 (638) | 244 (638) |
| 2024 | -437.639 (737) | 200 (737) |
| 2025 | -409.472 (726) | 250 (726) |
| 2026 | -32.544 (510) | 106 (510) |

### NSXUSD 60m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| — | — | — |

### XAUUSD 1m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| 2020 | -2469.169 (382) | 150 (382) |
| 2021 | -18818.866 (1871) | 724 (1871) |
| 2022 | -19467.180 (1858) | 682 (1858) |
| 2023 | -31484.781 (1736) | 577 (1736) |
| 2024 | -23298.651 (1952) | 729 (1952) |
| 2025 | -6590.433 (1888) | 624 (1888) |
| 2026 | -2375.130 (1457) | 479 (1457) |

### XAUUSD 3m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| 2020 | -773.148 (223) | 43 (223) |
| 2021 | -5878.395 (1100) | 467 (1100) |
| 2022 | -4548.361 (1035) | 311 (1035) |
| 2023 | -4542.812 (934) | 373 (934) |
| 2024 | -4727.061 (1095) | 364 (1095) |
| 2025 | -1368.819 (1070) | 285 (1070) |
| 2026 | -384.884 (839) | 235 (839) |

### XAUUSD 5m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| 2020 | -466.692 (156) | 35 (156) |
| 2021 | -4572.640 (787) | 293 (787) |
| 2022 | -2615.743 (761) | 218 (761) |
| 2023 | -2273.701 (707) | 293 (707) |
| 2024 | -2387.169 (760) | 196 (760) |
| 2025 | -1709.481 (731) | 215 (731) |
| 2026 | -218.124 (640) | 197 (640) |

### XAUUSD 60m
| Year | Configured | Zero-cost |
| ---: | ---: | ---: |
| — | — | — |

## Per-session splits

Session values are configured net R / zero-cost net R; trades are in parentheses.

### NSXUSD 1m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| asia | -20682.234 (4662) | 1431 (1431) |
| closed | -1939.591 (1789) | 274 (274) |
| london | -7085.150 (4084) | 1273 (1273) |
| new_york | -2813.199 (3202) | 981 (981) |

### NSXUSD 3m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| asia | -5598.201 (2091) | 872 (872) |
| closed | -295.951 (951) | 115 (115) |
| london | -3710.329 (2465) | 817 (817) |
| new_york | -2066.440 (2091) | 724 (724) |

### NSXUSD 5m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| asia | -2773.573 (1522) | 630 (630) |
| closed | -79.383 (759) | 109 (109) |
| london | -1046.715 (1632) | 506 (506) |
| new_york | -359.780 (1505) | 355 (355) |

### NSXUSD 60m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| — | — | — |

### XAUUSD 1m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| asia | -57216.331 (4995) | 2340 (2340) |
| closed | -33583.125 (2642) | 687 (687) |
| london | -16710.879 (3612) | 1162 (1162) |
| new_york | -9366.703 (2602) | 688 (688) |

### XAUUSD 3m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| asia | -14148.119 (2844) | 1311 (1311) |
| closed | -2495.672 (1142) | 87 (87) |
| london | -6286.204 (2371) | 800 (800) |
| new_york | -3257.071 (1678) | 474 (474) |

### XAUUSD 5m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| asia | -8528.925 (2076) | 934 (934) |
| closed | -802.177 (710) | -19 (-19) |
| london | -5097.383 (1714) | 623 (623) |
| new_york | -2938.045 (1320) | 393 (393) |

### XAUUSD 60m
| UTC session tag | Configured | Zero-cost |
| --- | ---: | ---: |
| — | — | — |

## Safety and provenance

{"broker_calls": 0, "lake_writes": 0, "orders": 0, "production_state_writes": 0, "sealed_holdout_read": false, "tuning": false}

The holdout guard was exercised before reading the lake and the requested end is before the sealed holdout boundary. No broker, order, tuning, gate, production-state, or lake write path is present in this diagnostic.

Pod provenance: `python /app/scripts/lab_t180_hist_tai.py --lake /workspace/data/lake --start 2020-10-17 --end 2026-10-17 --holdout-start 2026-10-17T04:00:00+00:00` (exit 0).

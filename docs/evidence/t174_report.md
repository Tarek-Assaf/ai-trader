# T-174 Renko setup diagnostic

**Verdict:** Renko setup explains it no; best variant 3m, epoch, closed: owner enters 4 of 31 within 2 bars of a same-direction cross; matched exact/+/-1/+/-2/+/-3 = 1/3/4/4 of 31 (Open/Close baseline 1/3/4/4); any variant >=25 of 31 within +/-2: no. This gain is below 15/31 and is noise across 8 variants; it is not a finding. The next likely cause is refresh-entry timing, a different data source (NQ1! futures versus NSXUSD CFD bars), or a different Pine version; no further variant was tested.

The registered grid contains 8 replay-only variants on NSXUSD 3m bars with seven days of full warm-up. Renko uses close-based bricks, ATR(3) frozen at brick formation, and a two-brick reversal. Closed mode uses the last completed source bar; forming mode includes the current source-bar prefix. The 54m source is session-anchored by epoch or New York midnight; the 3m source uses each chart bar. No broker, orders, holdout, lake writes, production state, tuning or strategy-search activity occurred.

## Per-variant results

```text
[
  {
    "atr_timeframe": "54m",
    "anchor": "epoch",
    "mode": "closed",
    "port_trades": 0,
    "renko_bricks": 34,
    "renko_crosses": 7,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 0,
      "within_3": 0
    },
    "cross_timing": {
      "within_2_bars_same_direction": 0,
      "median_same_direction_bars_ago": 55,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 0,
        "3-5": 0,
        "6-10": 0,
        "11-20": 0,
        "21-60": 1,
        "none": 30
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 55
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": null
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 0,
        "opposite": 0,
        "none": 31
      },
      "ha_state": {
        "same": 16,
        "opposite": 13,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "54m",
    "anchor": "epoch",
    "mode": "forming",
    "port_trades": 0,
    "renko_bricks": 49,
    "renko_crosses": 9,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 0,
      "within_3": 0
    },
    "cross_timing": {
      "within_2_bars_same_direction": 1,
      "median_same_direction_bars_ago": 44,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 1,
        "3-5": 0,
        "6-10": 0,
        "11-20": 0,
        "21-60": 2,
        "none": 28
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": 60
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 44
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": null
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 0,
        "opposite": 0,
        "none": 31
      },
      "ha_state": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "54m",
    "anchor": "ny_midnight",
    "mode": "closed",
    "port_trades": 3,
    "renko_bricks": 29,
    "renko_crosses": 7,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 1,
      "within_3": 1
    },
    "cross_timing": {
      "within_2_bars_same_direction": 0,
      "median_same_direction_bars_ago": null,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 0,
        "3-5": 0,
        "6-10": 0,
        "11-20": 0,
        "21-60": 0,
        "none": 31
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": null
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 0,
        "opposite": 0,
        "none": 31
      },
      "ha_state": {
        "same": 13,
        "opposite": 16,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "54m",
    "anchor": "ny_midnight",
    "mode": "forming",
    "port_trades": 0,
    "renko_bricks": 37,
    "renko_crosses": 7,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 0,
      "within_3": 0
    },
    "cross_timing": {
      "within_2_bars_same_direction": 0,
      "median_same_direction_bars_ago": 17,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 0,
        "3-5": 0,
        "6-10": 0,
        "11-20": 1,
        "21-60": 0,
        "none": 30
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 17
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": null
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 0,
        "opposite": 0,
        "none": 31
      },
      "ha_state": {
        "same": 14,
        "opposite": 15,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "3m",
    "anchor": "epoch",
    "mode": "closed",
    "port_trades": 35,
    "renko_bricks": 791,
    "renko_crosses": 181,
    "matches": {
      "within_0": 1,
      "within_1": 3,
      "within_2": 4,
      "within_3": 4
    },
    "cross_timing": {
      "within_2_bars_same_direction": 4,
      "median_same_direction_bars_ago": 8,
      "same_direction_histogram": {
        "0": 1,
        "1-2": 3,
        "3-5": 8,
        "6-10": 6,
        "11-20": 6,
        "21-60": 5,
        "none": 2
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": 25
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": 60
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 7
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": 16
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 11
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": 14
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 35
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": 17
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 30
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 0
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 10
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": 34
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 1,
        "opposite": 0,
        "none": 30
      },
      "ha_state": {
        "same": 28,
        "opposite": 1,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "3m",
    "anchor": "epoch",
    "mode": "forming",
    "port_trades": 0,
    "renko_bricks": 791,
    "renko_crosses": 181,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 0,
      "within_3": 0
    },
    "cross_timing": {
      "within_2_bars_same_direction": 2,
      "median_same_direction_bars_ago": 8.5,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 2,
        "3-5": 7,
        "6-10": 8,
        "11-20": 7,
        "21-60": 4,
        "none": 3
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 7
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": 26
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": 17
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": 15
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 36
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": 18
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": 13
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": 13
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 31
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 11
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": 35
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 0,
        "opposite": 3,
        "none": 28
      },
      "ha_state": {
        "same": 25,
        "opposite": 4,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "3m",
    "anchor": "ny_midnight",
    "mode": "closed",
    "port_trades": 35,
    "renko_bricks": 791,
    "renko_crosses": 181,
    "matches": {
      "within_0": 1,
      "within_1": 3,
      "within_2": 4,
      "within_3": 4
    },
    "cross_timing": {
      "within_2_bars_same_direction": 4,
      "median_same_direction_bars_ago": 8,
      "same_direction_histogram": {
        "0": 1,
        "1-2": 3,
        "3-5": 8,
        "6-10": 6,
        "11-20": 6,
        "21-60": 5,
        "none": 2
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": 25
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": 60
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 7
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": 16
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 11
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": 14
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 35
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": 17
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 30
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 0
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 10
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": 34
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 1,
        "opposite": 0,
        "none": 30
      },
      "ha_state": {
        "same": 28,
        "opposite": 1,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  },
  {
    "atr_timeframe": "3m",
    "anchor": "ny_midnight",
    "mode": "forming",
    "port_trades": 0,
    "renko_bricks": 791,
    "renko_crosses": 181,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 0,
      "within_3": 0
    },
    "cross_timing": {
      "within_2_bars_same_direction": 2,
      "median_same_direction_bars_ago": 8.5,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 2,
        "3-5": 7,
        "6-10": 8,
        "11-20": 7,
        "21-60": 4,
        "none": 3
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 7
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": 26
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-08T23:03:00+00:00",
          "direction": "short",
          "bar_index": 1313,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": 17
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-11T22:18:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T00:27:00+00:00",
          "direction": "long",
          "bar_index": null,
          "same_direction_cross_bars_ago": null
        },
        {
          "owner_time_utc": "2026-10-12T02:30:00+00:00",
          "direction": "long",
          "bar_index": 1813,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-13T14:57:00+00:00",
          "direction": "short",
          "bar_index": 2515,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": 15
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 36
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-14T22:24:00+00:00",
          "direction": "short",
          "bar_index": 3118,
          "same_direction_cross_bars_ago": 18
        },
        {
          "owner_time_utc": "2026-10-15T04:30:00+00:00",
          "direction": "short",
          "bar_index": 3235,
          "same_direction_cross_bars_ago": 13
        },
        {
          "owner_time_utc": "2026-10-15T05:27:00+00:00",
          "direction": "short",
          "bar_index": 3254,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-15T12:45:00+00:00",
          "direction": "short",
          "bar_index": 3395,
          "same_direction_cross_bars_ago": 13
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 31
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 11
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": 35
        }
      ]
    },
    "direction_at_owner_bar": {
      "direction": {
        "same": 0,
        "opposite": 3,
        "none": 28
      },
      "ha_state": {
        "same": 25,
        "opposite": 4,
        "none": 2
      },
      "vwap_side": {
        "same": 18,
        "opposite": 11,
        "none": 2
      },
      "ema_filter": {
        "same": 24,
        "opposite": 5,
        "none": 2
      },
      "adx_di": {
        "same": 19,
        "opposite": 3,
        "none": 9
      }
    }
  }
]
```

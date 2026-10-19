# T-172 HTF Heikin-Ashi anchor diagnostic

**Verdict:** anchor hypothesis yes; best variant ny_midnight, forming: owner enters 4 of 31 within 2 bars of a same-direction cross; entries matched exact/+/-1/+/-2/+/-3 = 1/4/5/5 of 31 (baseline 1/3/4/4); any variant >=25 of 31: no.

The eight registered variants are replay-only. If no variant improves cross timing above 8 of 31, the next likely cause is the registered anchors do not explain the gap; inspect the Renko/setupType path, originalFilter or owner inputs; no unregistered variant was tested.

## Variant results

```json
[
  {
    "anchor": "epoch",
    "mode": "closed",
    "port_closed_trades": 33,
    "matches": {
      "within_0": 1,
      "within_1": 3,
      "within_2": 4,
      "within_3": 4
    },
    "cross_timing": {
      "within_2_bars_same_direction": 1,
      "median_same_direction_bars_ago": 46.5,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 1,
        "3-5": 0,
        "6-10": 0,
        "11-20": 0,
        "21-60": 3,
        "none": 27
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
          "same_direction_cross_bars_ago": 44
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
          "same_direction_cross_bars_ago": 49
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
          "same_direction_cross_bars_ago": 57
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
          "same_direction_cross_bars_ago": 2
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
    }
  },
  {
    "anchor": "epoch",
    "mode": "forming",
    "port_closed_trades": 0,
    "matches": {
      "within_0": 0,
      "within_1": 0,
      "within_2": 0,
      "within_3": 0
    },
    "cross_timing": {
      "within_2_bars_same_direction": 1,
      "median_same_direction_bars_ago": 19.0,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 1,
        "3-5": 2,
        "6-10": 5,
        "11-20": 3,
        "21-60": 9,
        "none": 11
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 55
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": 7
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": 12
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 21
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
          "same_direction_cross_bars_ago": 39
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
          "same_direction_cross_bars_ago": 6
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
          "same_direction_cross_bars_ago": 44
        },
        {
          "owner_time_utc": "2026-10-13T17:36:00+00:00",
          "direction": "short",
          "bar_index": 2566,
          "same_direction_cross_bars_ago": 44
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 41
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 18
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 20
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
          "same_direction_cross_bars_ago": 45
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
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 24
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 39
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
    }
  },
  {
    "anchor": "cme_session",
    "mode": "closed",
    "port_closed_trades": 37,
    "matches": {
      "within_0": 1,
      "within_1": 3,
      "within_2": 5,
      "within_3": 5
    },
    "cross_timing": {
      "within_2_bars_same_direction": 0,
      "median_same_direction_bars_ago": 25,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 0,
        "3-5": 1,
        "6-10": 2,
        "11-20": 2,
        "21-60": 8,
        "none": 18
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 48
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
          "same_direction_cross_bars_ago": 48
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
          "same_direction_cross_bars_ago": 23
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
          "same_direction_cross_bars_ago": 20
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
          "same_direction_cross_bars_ago": 30
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 28
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 4
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
          "same_direction_cross_bars_ago": 12
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
          "same_direction_cross_bars_ago": 39
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 7
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 41
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 10
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 25
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
    }
  },
  {
    "anchor": "cme_session",
    "mode": "forming",
    "port_closed_trades": 35,
    "matches": {
      "within_0": 1,
      "within_1": 4,
      "within_2": 5,
      "within_3": 5
    },
    "cross_timing": {
      "within_2_bars_same_direction": 2,
      "median_same_direction_bars_ago": 11,
      "same_direction_histogram": {
        "0": 1,
        "1-2": 1,
        "3-5": 6,
        "6-10": 3,
        "11-20": 3,
        "21-60": 9,
        "none": 8
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 35
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 4
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 6
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
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": 41
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
          "same_direction_cross_bars_ago": 56
        },
        {
          "owner_time_utc": "2026-10-09T14:30:00+00:00",
          "direction": "short",
          "bar_index": 1612,
          "same_direction_cross_bars_ago": 19
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 58
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
          "same_direction_cross_bars_ago": 0
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 7
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
          "same_direction_cross_bars_ago": 48
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
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 30
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 3
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
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 21
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
          "same_direction_cross_bars_ago": 11
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 26
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 14
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": 38
        }
      ]
    }
  },
  {
    "anchor": "ny_midnight",
    "mode": "closed",
    "port_closed_trades": 37,
    "matches": {
      "within_0": 1,
      "within_1": 3,
      "within_2": 5,
      "within_3": 5
    },
    "cross_timing": {
      "within_2_bars_same_direction": 1,
      "median_same_direction_bars_ago": 31,
      "same_direction_histogram": {
        "0": 1,
        "1-2": 0,
        "3-5": 0,
        "6-10": 0,
        "11-20": 2,
        "21-60": 8,
        "none": 20
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 15
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
          "same_direction_cross_bars_ago": 48
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
          "same_direction_cross_bars_ago": 29
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
          "same_direction_cross_bars_ago": 36
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 34
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
          "same_direction_cross_bars_ago": 0
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
          "same_direction_cross_bars_ago": 28
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 46
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 41
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 16
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 31
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
    }
  },
  {
    "anchor": "ny_midnight",
    "mode": "forming",
    "port_closed_trades": 35,
    "matches": {
      "within_0": 1,
      "within_1": 4,
      "within_2": 5,
      "within_3": 5
    },
    "cross_timing": {
      "within_2_bars_same_direction": 4,
      "median_same_direction_bars_ago": 10,
      "same_direction_histogram": {
        "0": 2,
        "1-2": 2,
        "3-5": 5,
        "6-10": 4,
        "11-20": 5,
        "21-60": 7,
        "none": 6
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 33
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
          "same_direction_cross_bars_ago": 9
        },
        {
          "owner_time_utc": "2026-10-07T22:21:00+00:00",
          "direction": "long",
          "bar_index": 853,
          "same_direction_cross_bars_ago": 54
        },
        {
          "owner_time_utc": "2026-10-08T01:51:00+00:00",
          "direction": "long",
          "bar_index": 923,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": 10
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 21
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
          "same_direction_cross_bars_ago": 20
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 0
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
          "same_direction_cross_bars_ago": 0
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 5
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
          "same_direction_cross_bars_ago": 2
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 36
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 18
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 18
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 4
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
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 21
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
          "same_direction_cross_bars_ago": 11
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 26
        },
        {
          "owner_time_utc": "2026-10-16T13:18:00+00:00",
          "direction": "short",
          "bar_index": 3866,
          "same_direction_cross_bars_ago": 19
        },
        {
          "owner_time_utc": "2026-10-16T14:42:00+00:00",
          "direction": "short",
          "bar_index": 3890,
          "same_direction_cross_bars_ago": 43
        }
      ]
    }
  },
  {
    "anchor": "utc_midnight",
    "mode": "closed",
    "port_closed_trades": 24,
    "matches": {
      "within_0": 1,
      "within_1": 1,
      "within_2": 2,
      "within_3": 2
    },
    "cross_timing": {
      "within_2_bars_same_direction": 1,
      "median_same_direction_bars_ago": 25.5,
      "same_direction_histogram": {
        "0": 1,
        "1-2": 0,
        "3-5": 0,
        "6-10": 2,
        "11-20": 1,
        "21-60": 8,
        "none": 19
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 44
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
          "same_direction_cross_bars_ago": 38
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
          "same_direction_cross_bars_ago": 37
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
          "same_direction_cross_bars_ago": 17
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
          "same_direction_cross_bars_ago": 27
        },
        {
          "owner_time_utc": "2026-10-13T18:21:00+00:00",
          "direction": "long",
          "bar_index": 2580,
          "same_direction_cross_bars_ago": 24
        },
        {
          "owner_time_utc": "2026-10-14T05:24:00+00:00",
          "direction": "short",
          "bar_index": 2793,
          "same_direction_cross_bars_ago": 0
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
          "same_direction_cross_bars_ago": 8
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
          "same_direction_cross_bars_ago": 35
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 53
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
          "same_direction_cross_bars_ago": 6
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 21
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
    }
  },
  {
    "anchor": "utc_midnight",
    "mode": "forming",
    "port_closed_trades": 9,
    "matches": {
      "within_0": 1,
      "within_1": 1,
      "within_2": 2,
      "within_3": 2
    },
    "cross_timing": {
      "within_2_bars_same_direction": 3,
      "median_same_direction_bars_ago": 8,
      "same_direction_histogram": {
        "0": 0,
        "1-2": 3,
        "3-5": 6,
        "6-10": 2,
        "11-20": 2,
        "21-60": 8,
        "none": 10
      },
      "rows": [
        {
          "owner_time_utc": "2026-10-06T20:03:00+00:00",
          "direction": "short",
          "bar_index": 577,
          "same_direction_cross_bars_ago": 37
        },
        {
          "owner_time_utc": "2026-10-06T22:45:00+00:00",
          "direction": "short",
          "bar_index": 625,
          "same_direction_cross_bars_ago": 5
        },
        {
          "owner_time_utc": "2026-10-07T01:18:00+00:00",
          "direction": "short",
          "bar_index": 676,
          "same_direction_cross_bars_ago": 36
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
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-08T14:45:00+00:00",
          "direction": "long",
          "bar_index": 1158,
          "same_direction_cross_bars_ago": 28
        },
        {
          "owner_time_utc": "2026-10-08T15:09:00+00:00",
          "direction": "short",
          "bar_index": 1166,
          "same_direction_cross_bars_ago": 45
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
          "same_direction_cross_bars_ago": 34
        },
        {
          "owner_time_utc": "2026-10-09T16:30:00+00:00",
          "direction": "long",
          "bar_index": 1650,
          "same_direction_cross_bars_ago": 4
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
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-13T11:00:00+00:00",
          "direction": "short",
          "bar_index": 2438,
          "same_direction_cross_bars_ago": 3
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
          "same_direction_cross_bars_ago": 6
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
          "same_direction_cross_bars_ago": 8
        },
        {
          "owner_time_utc": "2026-10-14T08:54:00+00:00",
          "direction": "short",
          "bar_index": 2863,
          "same_direction_cross_bars_ago": 18
        },
        {
          "owner_time_utc": "2026-10-14T10:18:00+00:00",
          "direction": "short",
          "bar_index": 2891,
          "same_direction_cross_bars_ago": 26
        },
        {
          "owner_time_utc": "2026-10-14T12:18:00+00:00",
          "direction": "long",
          "bar_index": 2931,
          "same_direction_cross_bars_ago": 3
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
          "same_direction_cross_bars_ago": 3
        },
        {
          "owner_time_utc": "2026-10-15T13:42:00+00:00",
          "direction": "short",
          "bar_index": 3413,
          "same_direction_cross_bars_ago": 17
        },
        {
          "owner_time_utc": "2026-10-16T00:57:00+00:00",
          "direction": "long",
          "bar_index": 3628,
          "same_direction_cross_bars_ago": 1
        },
        {
          "owner_time_utc": "2026-10-16T10:12:00+00:00",
          "direction": "short",
          "bar_index": 3809,
          "same_direction_cross_bars_ago": 24
        },
        {
          "owner_time_utc": "2026-10-16T11:03:00+00:00",
          "direction": "short",
          "bar_index": 3824,
          "same_direction_cross_bars_ago": 39
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
    }
  }
]
```

Full per-entry timing, condition counts and safety counters are in the JSON artifact.

Safety counters are all zero/false; no broker, orders, holdout, lake-write, production-state, tuning or strategy-search activity.

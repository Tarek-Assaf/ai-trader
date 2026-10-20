# T-212: No feed-stall alerts during the scheduled maintenance break (code, small)

## Why
Owner got repeated "feed stalled / recovered" Telegram pairs between 00:00 and 01:00 Riyadh (21:00-22:00Z, the 17:00-18:00 New York
break). Bars are sparse then, so the T-211 stall rule fires although nothing is wrong.

## Do
1. In `CapitalDataRecorder._update_feed_stall` (src/trader/data/recorder.py) treat the scheduled break as expected silence: do not set
   `feed_stalled` and send no stall or recovery alert while the clock is inside the maintenance break, using the existing market-hours logic
   (America/New_York, DST aware, the same helper T-197 uses for the Tai CME break). Re-arm the 5-minute rule after the break ends.
2. Outside the break nothing changes: 5 minute threshold, 30 minute repeat, recovery message.
3. Unit tests with a fake clock: inside the break no alert; just after the break a stall still alerts; DST change day (first Sunday of November).
4. Ruff (line 100), mypy, CI green.

## Rules
No change to orders, dry-run, risk or gate files, or the Tai stale-feed guard.

## Acceptance
Tests pass; one log entry; turn to claude; stop.

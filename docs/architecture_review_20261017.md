# Architecture review, 2026-10-17 (Claude, requested by the owner)

Scope: ARCHITECTURE.md v3 compared with the repository, the handoff file (rev 398) and the evidence
so far. Facts below come from those files; "not verified" marks what I did not check on the live Pod.

## 1. Verdict
The platform is built to the blueprint: all 26 requirements have done tasks, the safety design
(LLM never touches the broker, deterministic kill path, hard risk ceilings, sealed holdout) is in
place, CI is strict, and the handoff loop works. What is missing is not infrastructure. It is the
parts that make the agent a trader: a strategy that has earned the right to trade, the market-state
logic that chooses between strategies, and proof that the execution path works the way the owner
trades.

## 2. Gaps against the blueprint (ranked)

### A. No strategy has qualified (R-03, R-04, R-06) - highest impact
Nothing has passed Gate 1 net of cost. Rounds A to D, the Tai replay, the exit study and the
recorded-data run were weak or negative after costs. The blueprint's route (Idea Generator, lab,
stage ladder) can run, but it has nothing to promote. Recommendation: stop adding fixed idea
families; move to (1) the movement-matching study (T-146, in review), (2) a regime-aware lab that
reports every strategy by market state (trend/range/high-volatility, session), and (3) a selector
that picks among validated strategies by state. This is the owner's "toolbox" idea and is not in
ARCHITECTURE.md yet.

### B. Learning loop is not closed (R-03, R-21)
The blueprint has post-mortem and performance agents, but nothing turns outcomes into new
validated hypotheses automatically, and the owner coaching loop is missing. T-150 (analysis journal)
is built. Missing: image input (below), the link from journal lessons to the Idea Generator, and a
rule that lessons only become tests, never live changes.

### C. Visual input is not in the blueprint (owner request)
Charts out exist (viz, Telegram send_photo). Charts in do not: Telegram receives no photos and no
code reads images. Recommendation: new task T-153: receive photo in Telegram, store hash, have the
model read symbol/timeframe/levels/trade/result into the journal before the outcome, reply with a
code-drawn chart from recorded data. Read-only, inside the existing LLM caps, needs a redeploy.

### D. Cost and basis are still assumptions (R-06, R-08)
The lab assumes 4 pts (NSXUSD) and 1.6 (XAUUSD) per round trip. Recorded real median spreads are
1.8 (NAS100) and 0.5 (XAUUSD). T-017 (HistData vs Capital.com basis) and T-044 (execution and data
quality analysts) are still blocked on recorder data, which now exists (since 2026-10-05).
Recommendation: unblock T-017 and T-044 now, then re-state all earlier results with the real cost
model, per session. Strategies tested on HistData are traded on Capital.com; the basis between them
has never been measured.

### E. Execution path is unproven for the owner's exits (R-10, R-11)
The owner exits by scaling out (a third at 1R, 2R, 3R, stop to entry after the first). The paper
broker has close_partial; the Capital.com adapter has no partial close. The Capital.com
cash/equity/margin mapping is also unverified (B-006) because only one flat-account demo round trip
was done. Recommendation: add partial close and stop-move to the Capital.com adapter with a
contract test, then a supervised demo round trip with a position open long enough to capture the
account snapshot.

### F. Deployment depends on the owner and keeps breaking (R-23)
Codex cannot redeploy (RunPod 403). Image mismatches and missing manifests blocked T-129, T-133 and
T-136 earlier, and the live Pod has sat on old images. Recommendation: one documented deploy
command or script the owner runs, a /health version check that the agents read before any Pod
task, and a rule that a task which needs a new image says so in its first line.

### G. The unattended soak has not been shown (Phase 8)
The roadmap requires four weeks unattended on demo with no safety incident. The soak monitor exists
(T-092). I have not verified that a soak is running now or what the Pod's state is. Recommendation:
confirm and start the clock only after A to E, otherwise the four weeks test nothing.

## 3. Process and housekeeping
1. Codex has no ruff or mypy and CI uses ruff 0.9.6, so most Codex PRs fail CI once and cost a
   review round. Provide a one-command pre-push check (make check inside the dev container) and
   require its output in the completion note.
2. The handoff file is 1.4 MB with 404 log entries. Move closed history to an immutable archive
   file and keep the live file short; the validator must accept the split.
3. ARCHITECTURE.md is v3 and predates D-029 to D-040: add the regime selector, image input, the
   recorder as a first-class data source, the real cost model and the parked Tai work.
4. Security: the owner pasted Pod terminal URLs with access tokens in chat. Rotate those tokens
   when each task ends and never reuse an old link.
5. Q-022 (recorder multi-hour health) is still open although the recorder has run 11 days. Close it.
6. Open owner decisions still listed in section 15: hosted LLM budget and the jurisdiction check
   for Capital.com before any live money.

## 4. Recommended order
1. Finish T-146 (in review) and read its out-of-sample verdict.
2. Unblock T-017 and T-044 with the recorder data; re-state results at real cost.
3. T-153 image input and the journal-to-lab link (needs redeploy, announced in advance).
4. Capital.com partial close and stop-move, then a supervised demo round trip.
5. Regime-aware lab report and the strategy selector.
6. Deploy script, pre-push check, handoff archive, ARCHITECTURE.md v4.
7. Only then start the four-week soak.

# Work queue for Codex (pod-free work first)

Owner instruction (2026-10-03): everything runs without the owner except steps that need the RunPod pod. Codex keeps taking the next unblocked task from this list without waiting to be told, one Codex session per task. Claude reviews each PR (tiers in `docs/process_fastlane.md`) and enables auto-merge; Codex starts the next task as soon as its PR is open if the next task does not build on unreviewed Full-tier code.

## Order (all runnable without the pod)
1. T-015 market hours service (Light, batch with T-016)
2. T-016 instruments.yaml and mapper (Light)
3. T-020 Risk Guardian (Full; spec `docs/specs/phase2.md`, numbers in `config/risk.yaml`)
4. T-021 position sizing (after T-016), T-022 quality score (after T-020)
5. T-040 analysis engine, T-041 research sources and sanitizer, T-042 LLMClient with mock (Light/Full per tier doc; spec `docs/specs/phase4.md`; no live API calls, use MockLLM)
6. T-024 CapitalComBroker adapter (Full; demo only, tests with recorded fixtures), T-023 PaperBroker uses defaults until T-017 gives measured basis/spread
7. T-025 P&L engine, T-026 reconciler, T-027 kill switch (Full)
8. T-060 backup job (Full; spec `docs/specs/phase6_7.md`), T-051 StrategySpec sandbox engine
9. Phase 3 (T-030, T-031, T-032, T-033, T-034) after T-027, specs `docs/specs/phase3.md` and `settings_service.md`

## Pod session (owner decision 2026-10-04: use the pod for everything that is not live trading)
Once the owner starts the pod, run all of this on it, in this order, with owner steps limited to starting the pod and pasting summaries:
1. Recorder soak test on the demo account (Q-022) and the full HistData range import plus rerun (Q-024, finishes T-080).
2. T-017 basis and spread measurement from recorder data, then T-044 execution-quality analysts.
3. T-050 backtest engine runs, T-051/T-052/T-053 Strategy Lab work, T-040 analysis on the real lake data, paper trading (PaperBroker, no live orders), backups (T-060) to GitHub and HuggingFace.
Codex prepares scripts and one-command runbooks for each so the owner only pastes a command or a summary. Anything that places orders on the broker, even on demo, waits for an explicit owner go-ahead.

## Not before the owner says so
- Live or demo order placement through the CapitalComBroker (T-024 live tests).
- Anthropic API key and budget (Phase 4 live LLM calls); use MockLLM until then.

## Rules
- If a task is blocked, record why in the handoff and take the next one. Ask the owner only for pod steps, API keys or budgets, and group all such asks into one question.
- Follow `docs/review_checklist.md`. No new dependencies without recording approval; ask only when the dependency is a heavy or risky one.

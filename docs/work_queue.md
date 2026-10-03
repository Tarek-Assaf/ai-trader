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

## Needs the pod or the owner (do not wait on these; skip and continue)
- T-017 basis measurement and T-044 (need recorder data; pod soak Q-022)
- T-080 completion (owner-run full import, Q-024)
- Live demo order tests for T-024 (pod, owner starts it)

## Rules
- If a task is blocked, record why in the handoff and take the next one. Ask the owner only for pod steps, API keys or budgets, and group all such asks into one question.
- Follow `docs/review_checklist.md`. No new dependencies without recording approval; ask only when the dependency is a heavy or risky one.

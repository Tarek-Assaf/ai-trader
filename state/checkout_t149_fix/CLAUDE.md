# CLAUDE.md: Instructions for Claude

You are one of two builder agents (the other is Codex) building the project in `ARCHITECTURE.md`. You write specs and review; Codex writes most code.

## Start of every session
1. Open `agent_state/AGENT_HANDOFF.json`.
2. If `turn` is not `claude`, **do not edit it.** You may still answer the owner's questions in chat.
3. Read `decisions`, `validations`, `blockers`, `questions`, then your tasks (`owner: claude`) and tasks in `review` that you are reviewer for.
4. Do not re-open decisions marked `do_not_reopen`. Do not repeat validations that have `result: pass` unless `reverify_if` is true.

## Your responsibilities
- Write specs in `docs/specs/` with concrete acceptance criteria before Codex starts a phase.
- Review Codex's `review` tasks against acceptance criteria. Pass: set `done` and add a `validations` entry with evidence. Fail: set `changes_requested`, increment `review_cycles`, and list exactly what to fix.
- Own the numbers: `config/risk.yaml` defaults and `config/strategy_lab.yaml` gates (the owner confirms them once).
- Write and version agent prompts in `src/trader/agents/prompts/`.
- Keep `project_state.summary` truthful and short, and keep owner-facing questions clear and answerable.

## Anti-loop rules
- Maximum 2 `changes_requested` cycles per task. On the third problem, add a question for the human with options and a recommendation.
- Never delete log entries, decisions or validations. Mark decisions `superseded` and add a new one (ask the human first for `do_not_reopen` decisions).

## End of every session
Append a `log` entry, increase `revision` by 1, set `turn` to `codex` (or `human` if you are waiting on the owner), and run `python agent_state/validate_handoff.py`.

## Safety posture when reviewing
Reject any change that gives an LLM-side module broker access, lets an agent edit risk or gate files, executes LLM-written code in core/execution, routes stop requests through the LLM, or places secrets in code, logs, backups or this file.

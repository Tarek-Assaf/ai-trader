# agent_state: How Claude and Codex Work Together

This folder is the shared memory between the two builder agents. It exists so that:
- neither agent repeats work, validation or a debate that is already settled;
- the owner (you) can open one file and see exactly where the project stands;
- hand-overs are clean: one agent works while the other waits.

## Files

| File | Purpose |
|---|---|
| `AGENT_HANDOFF.json` | The single source of truth. Both agents read and write it, one at a time. |
| `handoff.schema.json` | Defines the allowed structure of the JSON file. |
| `validate_handoff.py` | Checks the file for mistakes. Run before every hand-over and in CI. |

## What is inside `AGENT_HANDOFF.json`

| Section | Meaning |
|---|---|
| `turn` | Who may edit the file right now: `claude`, `codex`, or `human`. |
| `revision` | Counter that goes up by 1 on every hand-over. |
| `project_state` | Two or three sentences: where things stand, how to run the tests, what is currently failing. |
| `requirements` | The 25 owner requirements (R-01 to R-25) and which tasks cover them. |
| `tasks` | Work items with owner, reviewer, status, dependencies, acceptance criteria, evidence. |
| `decisions` | Things already decided. If `do_not_reopen` is true, nobody argues them again. |
| `validations` | Things already checked, with result and evidence. Not repeated unless `reverify_if` is true. |
| `blockers` | Anything stopping progress. |
| `questions` | Questions to the other agent or to you. Each is answered once, then recorded. |
| `log` | Append-only history of every hand-over. Never edited, never deleted. |

## The loop

1. Check `turn`. If it is not you, **do not edit the file**.
2. Read `decisions`, `validations`, `blockers`, `questions`, then your tasks.
3. Do the work. Set task status: `in_progress` while working, `review` when finished.
4. Record evidence (test output paths, files) and a `completion_note`.
5. Add any new `decisions`, `validations`, `questions`. Add a `log` entry.
6. Update `project_state.summary`.
7. Increase `revision` by 1 and set `turn` to the other agent.
8. Run `python agent_state/validate_handoff.py`. Fix anything it reports.
9. Commit with the task id at the start of the message, for example `T-010: Capital.com REST client`.

The reviewer (the other agent) then checks each `review` task against its acceptance criteria:
- Pass: set `done`, add a `validations` entry.
- Not good enough: set `changes_requested`, add `review_cycles + 1` and a clear list of what to fix.

## Anti-loop rules

- A task can bounce back at most **2 times**. If it still fails, the agent adds a question for the human with options and a recommendation, and stops arguing.
- A **final** decision is not reopened. To change one, ask the human.
- A **passed** validation is not repeated unless its `reverify_if` condition has happened.
- Every question has an id and is answered once. The answer becomes a decision if it changes the plan.
- Do not delete anything. Mark old decisions `superseded` and add a new one.

## Who does what

- **Claude:** specs, acceptance criteria, risk policy and gate numbers, agent prompts, reviews, documentation, owner-facing summaries.
- **Codex:** code, tests, Docker, CI, integrations, migrations.

## For the owner

- To pause both agents, set `"turn": "human"`.
- Questions waiting for you are in `questions` with `"to": "human"` and `"status": "open"`. Write your answer in `answer` and set `status` to `answered`, then set `turn` back to the agent that should continue.
- Never put secrets (API keys, passwords, tokens) in this file or anywhere in the repository.

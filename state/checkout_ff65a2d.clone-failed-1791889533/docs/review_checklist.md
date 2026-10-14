# Self-review checklist for Codex (run before setting a task to `review`)

Recurring problems found in earlier reviews. Check each, tick it in your handoff note.

**Remote services**
- [ ] Every reply from a remote service is validated (status, shape, required fields). Silence or an empty reply is not success.
- [ ] Errors raised by your own consumer code are not masked as network errors or retried as if they were.
- [ ] Rate limiter or Redis failure fails closed (no request sent), with a test.

**Secrets and logging**
- [ ] No secret in code, logs, backups, exceptions, test output, handoff or docs. Third-party library DEBUG logging is capped or redacted. A test captures logs on the failure path.
- [ ] Owner-run scripts are demo-only, print redacted output, and say what they will do before doing it.

**Data and writes**
- [ ] Writes are idempotent (rerun does not duplicate). Time is UTC, bars are closed bars only, no look-ahead.
- [ ] File modes and ownership are preserved (scripts executable, non-root user can read/write what it needs).

**Tests**
- [ ] Each test proves what its name claims (for example a SIGTERM test really sends SIGTERM and checks the artifact). Failure cases are tested, not only the happy path.
- [ ] Anything touching safety has a test: stop paths with the LLM broken, no broker import under `trader/agents`, risk files not writable by agents.

**Scope and process**
- [ ] No new dependency, no GPU requirement, no paid service without asking in the handoff.
- [ ] Only the files in the task were changed. CI jobs (`quality`) are green on the branch.
- [ ] Handoff: `python agent_state/validate_handoff.py` passes; log entry appended; revision +1; turn set correctly; evidence filled in for each acceptance criterion.
- [ ] Do not enable auto-merge until Claude's approval is recorded on the branch.

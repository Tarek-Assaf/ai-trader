# Fast-lane process (owner-approved 2026-10-03)

Goal: fewer turn trades and less waiting, without weakening the safety rules in CLAUDE.md. Registered in the handoff as D-025 at Claude's next turn.

## 1. Review tiers
| Tier | Applies to | Claude reviews |
|---|---|---|
| Full | Anything touching risk, execution, broker, SettingsService, secrets, backups/restore, Docker/entrypoint, CI, agent permissions, prompts-loading | Every line, plus tests |
| Light | Data importers/resamplers/mappers, analysis, viz, reports, tests, fixtures, docs | Acceptance evidence, CI result, diff skim of risky spots (logging, writes, network) |

Codex states the tier in its handoff note; Claude may raise a task to Full. The 2-cycle limit and the safety posture in CLAUDE.md apply to both tiers.

## 2. Batching
Related small tasks may share one branch and one pull request (for example T-014 and T-015). Each task still has its own acceptance evidence.

## 3. Light-tier tasks may start before the previous review ends
Codex may start T-012, T-013, T-016 and other Light tasks while a Full task waits for review, on a separate branch. It must not build on unreviewed Full code.

## 4. Auto-merge
After Claude records approval on the branch, Claude enables auto-merge. `main` requires a pull request and the `quality` check. Nobody pushes to `main` directly except the owner; Claude docs changes go through pull requests too.

## 5. Owner-run steps
Codex bundles checks into one owner-run command per phase milestone and prints redacted, demo-only output.

## 6. Order change
After T-011 fixes: T-014 (recorder) first, then T-012, T-013, T-015, T-016, T-017.

## 7. Checks that never get skipped
Safety invariants (ARCHITECTURE 12), static import tests, secret-redaction tests, and the review checklist.

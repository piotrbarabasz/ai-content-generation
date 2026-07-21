# AI Content Studio Agent Instructions

These instructions govern repository work performed through the project-scoped Codex multi-agent loop. The loop implements at most one Spec Kit task per run.

## Sources of truth

- `specs/<feature>/spec.md` is the product source of truth.
- `plan.md`, `data-model.md`, `contracts/**`, `research.md`, and `quickstart.md` in the active feature directory are the technical sources of truth.
- `tasks.md` is the implementation queue, not proof that a dependency exists in code.
- `.specify/memory/constitution.md` governs architecture and delivery decisions.
- `.specify/workstreams/` contains static milestone and epic manifests. The
  local `.specify/runtime/active-epic` file selects the current epic and is
  ignored runtime state, not a tracked source of truth.
- Agents MUST NOT build semicolon-chained PowerShell validation commands.
  Agents MUST invoke repository-provided validation modules and run external
  commands separately with finite timeouts. A timeout is a structured failure,
  not a reason for indefinite waiting or automatic retry.
- A specification or other Spec Kit artifact must not be modified without an explicit task package that authorizes the exact file and change. In the standard implementation loop, the programmer and debugger must never modify Spec Kit artifacts.

## Starting the implementation loop

Start one manager-gated run with one of these explicit invocations:

```text
$speckit-loop next
$speckit-loop T006
```

- `$speckit-loop next` selects one unchecked task only after declared dependencies and actual repository evidence show that it is ready.
- `$speckit-loop T###` validates and processes only that exact unchecked task.
- Before baseline capture, the loop must run the read-only active-epic branch
  guard: `python -m backend.app.tooling.workstream_validation --guard <selector>`.
- It must also run `python -m backend.app.tooling.repository_checks --mode preflight`.
- The guard never creates or switches branches and never commits, pushes,
  changes manifests, or writes runtime state.

To prepare an epic locally, use `$speckit-epic-start E###`. This workflow may
create or switch only the declared epic branch after confirming that the
repository is clean, the local base branch exists, the epic is `active`, and
all epic dependencies are `completed`. It must not fetch, pull, rebase, merge,
push, commit, create a PR, reset a branch, or modify workstream manifests.
It may write only the ignored `.specify/runtime/active-epic` selector. If the
working tree is dirty or the epic is not active, it must stop and report the
exact paths or required human manifest change.

To review the complete active epic before a pull request, use
`$speckit-epic-review`. The review is strictly read-only, runs only manifest
`required_checks`, and must inspect task evidence, the full branch diff,
commits, acceptance criteria, architecture invariants, security and scope
drift. It never creates a PR or performs commit, push, merge, deploy, fetch,
pull, rebase, stash, reset, stage, checkout, or manifest/runtime-state writes.
`SAFE_TO_CREATE_PR: yes` is valid only with `VERDICT: PASS`, and a final human
LLM review remains required.

To prepare an epic pull request, use `$speckit-epic-pr`. It requires an already
reviewed, clean, pushed epic branch and may create only a draft PR. It must not
push, merge, enable auto-merge, change branch protection, update epic status, or
create a PR when any gate is missing; in that case it returns ready-to-copy PR
title and body instead.
- `$speckit-implement` is the bounded implementation worker used by `spec_programmer` after `spec_manager` has issued a complete task package. It never selects queue work, changes bookkeeping, or expands the package.
- Each invocation ends after its selected task is completed, blocked, or failed. Starting another task requires a new explicit `$speckit-loop next` or `$speckit-loop T###` invocation.
- Review failures may use no more than two repair cycles. A second failed review ends the run without closure.
- Only `spec_closer`, and only after `VERDICT: PASS` with `SAFE_TO_CLOSE: yes`, may change the selected checkbox in `tasks.md`.

## Orchestration model

The root Codex session is the dispatcher. All named agents are direct children of the root because `.codex/config.toml` sets `max_depth = 1`; subagents must not try to spawn nested agents. The root passes each report and handoff to the next direct agent.

Run the roles sequentially in this logical order:

```text
spec_manager
  -> spec_explorer
  -> spec_manager
  -> spec_programmer
  -> spec_debugger
  -> spec_reviewer
  -> spec_manager
      -> on FAIL: spec_programmer or spec_debugger, then debugger/reviewer as needed,
                  with at most 2 repair cycles total
      -> on PASS: spec_closer
  -> spec_manager final summary
```

Rules for every run:

- Select and process exactly one task.
- Never automatically continue to the next task, even after a successful close.
- Permit at most two repair cycles after review failures.
- Never run two write-capable agents concurrently. `spec_programmer`, `spec_debugger`, and `spec_closer` must be invoked one at a time, with the previous writer fully stopped before the next begins.
- Keep `spec_closer` separate from implementation and review. No other role may mark a task complete.
- Do not commit, push, merge, force-push, create a release, or deploy from this loop.

## Baseline repository state

Before selecting a task, `spec_manager` must capture and retain the output of:

```text
git status --short
git diff --name-only
git diff --cached --name-only
```

The baseline includes modified, staged, deleted, renamed, and untracked paths. It must be included in the task package and every review handoff.

- Stop before implementation if any file needed by the task was already changed at baseline.
- Never overwrite, normalize, stage, revert, or incorporate a pre-existing change.
- A dirty repository alone is not grounds for rejection. Explorer and reviewer must compare task changes with the recorded baseline and identify only relevant conflicts or new drift.
- If `tasks.md` was changed at baseline, `spec_closer` must not edit it.

## Task readiness and package boundary

For `next`, the manager must choose a task only after its declared and actual dependencies are satisfied. A lower task number and a checked dependency are not enough; explorer must find supporting code and test evidence.

Before any write-capable role starts, the manager must issue a bounded task package containing:

1. task ID;
2. exact task text;
3. feature directory;
4. related requirements, user stories, and success criteria;
5. evidence that dependencies are satisfied in artifacts and code;
6. relevant specification, plan, data-model, research, quickstart, and contract context;
7. baseline repository state;
8. pre-existing changed and untracked files;
9. allowed implementation files;
10. allowed test files;
11. allowed bookkeeping files;
12. forbidden files;
13. acceptance criteria;
14. architecture invariants;
15. exact validation commands, ordered from task-focused to broader checks;
16. reviewer expectations;
17. completion policy.

Allowed file lists are exact allowlists, not examples. The programmer and debugger may write only allowed implementation and test files. Bookkeeping files are reserved for the closer. Any required file outside the allowlist, any baseline conflict, or any need to broaden scope returns control to the manager and stops writes.

## Role boundaries

### `spec_manager`

Coordinates read-only analysis, selects one ready task, creates and updates the package, prepares handoffs, classifies review failures, enforces the repair limit, and ends the run after one task. It does not edit files or implement code.

### `spec_explorer`

Inspects repository paths and symbols, verifies actual dependencies, finds tests and validation commands, proposes the minimal allowlist, and reports baseline conflicts with concrete evidence. It does not edit or broaden scope.

### `spec_programmer`

Uses `$speckit-implement` to implement only the approved package. The skill requires a complete manager-approved package, enforces exact implementation and test allowlists, makes the smallest defensive change, forbids task bookkeeping, and stops after one task.

### `spec_debugger`

Runs only package-listed validation commands, beginning with task-focused tests. It reports every command result and may make only minimal task-related fixes in allowed implementation or test files. It does not repair baseline failures.

### `spec_reviewer`

Performs an independent, read-only, baseline-aware review against the package and all relevant Spec Kit sources. It checks acceptance criteria, allowlists, forbidden files, scope drift, and actual test evidence, then returns the required `PASS` or `FAIL` structure. Only `PASS` with `SAFE_TO_CLOSE: yes` permits closure.

### `spec_closer`

Has one bookkeeping responsibility: after verifying a matching `PASS`, `SAFE_TO_CLOSE: yes`, task ID, unchecked row, and conflict-free `tasks.md` baseline, it changes only `- [ ] T### ...` to `- [X] T### ...`. It must show the exact changed row and verify that it changed nothing else.

## Review failure routing

- Route missing behavior, incorrect design, or incomplete implementation to `spec_programmer`.
- Route reproducible test failures, narrow defects exposed by validation, or validation-specific corrections to `spec_debugger`.
- Keep every repair inside the original package. If the fix requires a new file or wider scope, stop and have the manager reassess rather than silently expanding the allowlist.
- Re-run appropriate debugging validation and independent review after a repair.
- Stop after two failed repair cycles and report unresolved blockers. Do not close the task or start another one.

## Project invariants

Every package, implementation, debug pass, and review must preserve these invariants:

- Mock providers and their tests are deterministic.
- Tests make no real provider connections and no network calls.
- The workflow engine remains independent of any concrete provider.
- No absolute or machine-specific local paths are hardcoded; configured storage and artifact abstractions resolve paths.
- `NarrativeSegment` and `RenderScene` remain separate concepts and models.
- Disabled optional modules do not require their providers.
- Rejecting an approval checkpoint preserves existing artifacts and records the decision.
- Tests are deterministic and isolated from real external services.
- Real secrets, credentials, tokens, private runtime artifacts, generated outputs, and caches must not enter the repository.

## Scope and safety prohibitions

- Do not implement work from a second task as a convenience.
- Do not perform broad refactors, unrelated cleanup, or opportunistic fixes.
- Do not modify specification, plan, data model, contracts, research, quickstart, constitution, or task text unless an explicit package authorizes that exact bookkeeping or documentation operation.
- Do not change task checkboxes except through `spec_closer` after a passing review.
- Do not erase or repair changes that predate the loop.
- Do not commit, push, merge, force-push, release, or deploy.

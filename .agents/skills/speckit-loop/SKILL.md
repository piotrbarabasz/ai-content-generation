---
name: "speckit-loop"
description: 'Run one dependency-ready Spec Kit implementation task through the repository''s manager-gated multi-agent workflow. Use when Codex should select the next ready task or execute one explicit T\d{3}[A-Z]? task with baseline isolation, bounded files, validation, independent review, and closer-only bookkeeping.'
metadata:
  author: "ai-content-generation"
  source: "project multi-agent workflow"
---


## User Input

```text
$ARGUMENTS
```

Use the root Codex thread as the sole orchestrator. Process exactly one task and stop.

## 1. Validate the selector

Trim `$ARGUMENTS` and accept only:

- no argument or `next`: select one dependency-ready unchecked task;
- an exact uppercase identifier matching `T\d{3}[A-Z]?`: select that task only.

For any other input, stop without edits and show:

```text
Usage:
  $speckit-loop next
  $speckit-loop T006
  $speckit-loop T006A
```

Never reinterpret free text as a task selector. Never continue to another task after the selected task completes, blocks, or fails.

## 2. Resolve the feature context

From the repository root, consume the JSON feature context returned by
`python -m backend.app.tooling.agent_task_preflight --selector <selector> --json`.
Use the report's `FEATURE_DIR`, `AVAILABLE_DOCS`, and optional artifact paths;
require absolute resolved paths and stop if `spec.md`, `plan.md`, or `tasks.md`
is missing.

Load:

- `FEATURE_DIR/spec.md`;
- `FEATURE_DIR/plan.md`;
- `FEATURE_DIR/tasks.md`;
- `FEATURE_DIR/data-model.md` when present;
- every file under `FEATURE_DIR/contracts/` when present;
- `FEATURE_DIR/research.md` when present;
- `FEATURE_DIR/quickstart.md` when present;
- `.specify/memory/constitution.md`.

Treat `spec.md` as product truth, the plan and supporting design documents as technical truth, and `tasks.md` as a queue rather than proof that code exists.

## 3. Enforce checklist gates

If `FEATURE_DIR/checklists/` exists, inspect every checklist item matching `- [ ]`, `- [x]`, or `- [X]`. Treat a checklist as mandatory unless it explicitly declares `optional: true` in YAML frontmatter or `Optional: true` in its header metadata.

If any mandatory checklist has unchecked items:

- stop before recording an implementation handoff or invoking an agent;
- make no edits;
- do not ask whether the checklist may be skipped;
- print each incomplete item with its checklist path, line number, and exact text.

Continue only when every mandatory checklist is complete.

## 4. Consume preflight, then implement

Before any agent is invoked, the root orchestrator runs
`python -m backend.app.tooling.agent_task_preflight --selector <selector> --json`
for the selected task selector. That report supplies the active epic,
branch, baseline inventory, readiness checks, and task selection. The manager
and explorer consume that report; they do not run repository validation or raw
Git inventory commands directly.

Preflight does not modify tracked repository files, Git history, branches,
tasks, or manifests. Its only permitted write is the ignored runtime baseline
file under `.specify/runtime/task-runs/`. If preflight fails, stop immediately
and do not invoke any agent.

`next` may consider only unchecked tasks in the active epic; an explicit task
ID matching `T\d{3}[A-Z]?` must belong to that epic. The one-task-per-run and
closer-only completion rules remain unchanged.

## 5. Consume the preflight baseline

The preflight report already contains the baseline inventory, including
tracked, staged, deleted, renamed, and untracked paths. Store that report in
the root thread and include it in every manager and reviewer handoff. Earlier
unrelated changes do not fail the loop by themselves. If implementation or
validation needs a path that was dirty at baseline, stop before
implementation. No agent may overwrite, absorb, normalize, revert, or claim
ownership of that path.

## 6. Select and explore one task

Invoke only direct configured subagents; `max_depth = 1` means subagents must not spawn other subagents.

Run this sequence serially on the happy path:

```text
spec_manager
  -> spec_explorer
  -> spec_manager
  -> PROGRAMMER_ROUTE
  -> agent_task_finalize --json
  -> spec_reviewer
  -> spec_manager
      -> on PASS: spec_closer
```

Never run two write-capable agents concurrently. `PROGRAMMER_ROUTE` must be
exactly `spec_programmer_fast` or `spec_programmer_high`.
`spec_debugger` is never part of the happy path. It is reserved only for a
real `FAIL` or `TIMEOUT` from the finalize report or a reviewer `FAIL` that
requires a minimal package-bounded fix. After every programmer or debugger
repair, the root orchestrator reruns
`python -m backend.app.tooling.agent_task_finalize --task <task> --json`
before sending evidence back to the reviewer. Never hand the reviewer a stale
finalize report.

### Manager selection pass

Give `spec_manager` the selector, feature context, complete task queue, and
the preflight report.

For `next`, require the manager to:

1. enumerate unchecked tasks;
2. evaluate declared dependencies;
3. verify those dependencies against actual code and tests;
4. nominate exactly one ready task without relying on the lowest ID alone.

For an explicit task ID matching `T\d{3}[A-Z]?`, require the manager to verify that the task exists, remains unchecked, and has satisfied declared and actual dependencies. Preserve the selector exactly as entered. Stop if any check fails.

### Explorer pass

Give `spec_explorer` the nominated task, feature context, and preflight report.
Require concrete path and symbol evidence for readiness, existing
implementation, tests, minimal file allowlists, and baseline conflicts. The
explorer must not edit or validate.

### Final manager pass

Return explorer findings to `spec_manager`. Require the manager either to stop with evidence or issue one final task package with exactly these fields:

```text
TASK_ID
TASK_TEXT
FEATURE_DIR
RELATED_REQUIREMENTS
DEPENDENCY_EVIDENCE
RELEVANT_ARTIFACTS
BASELINE_STATUS
PRE_EXISTING_DIRTY_FILES
ALLOWED_IMPLEMENTATION_FILES
ALLOWED_TEST_FILES
ALLOWED_BOOKKEEPING_FILES
FORBIDDEN_FILES
ACCEPTANCE_CRITERIA
ARCHITECTURE_INVARIANTS
VALIDATION_COMMANDS
REVIEWER_EXPECTATIONS
COMPLETION_POLICY
RISK_LEVEL
PROGRAMMER_ROUTE
HUMAN_CHECKPOINT_REQUIRED
```

Make implementation and test allowlists exact and minimal. List the selected `tasks.md` only under `ALLOWED_BOOKKEEPING_FILES`, and reserve it exclusively for `spec_closer`. It must remain forbidden to programmer and debugger. Stop if an allowed implementation or test path conflicts with the baseline.

## 7. Implement the package

Give `PROGRAMMER_ROUTE` only the final package plus the relevant `$speckit-implement` instruction.

Require the programmer to:

- implement exactly the selected package;
- edit only `ALLOWED_IMPLEMENTATION_FILES` and `ALLOWED_TEST_FILES`;
- make the smallest defensive change consistent with repository conventions;
- stop if another path or wider scope becomes necessary;
- leave `tasks.md` and every Spec Kit artifact unchanged;
- avoid unrelated refactors and pre-existing problems;
- avoid real provider and network calls;
- refrain from commit, push, merge, force-push, release, and deployment;
- stop after this task and return the `$speckit-implement` report.

If `RISK_LEVEL` is `critical`, stop before any programmer handoff until the human checkpoint is explicitly recorded. High-risk packages must carry the full architecture justification and exact allowlists in the package.

## 7. Debug and validate

Give `spec_debugger` the package and programmer report only when the finalize
report or reviewer report shows a real failure that can be repaired inside the
package. Run only the failing task-focused command or commands listed in the
finalize report. Never rerun passing checks and never run full `pytest` or
repository validation modules in the debugger. Commands may include, when
supported by the repository and selected by the manager:

```powershell
python -m pytest <task-focused-tests>
```

Do not make real provider calls or network requests. Require the debugger to
report every command, exit status, and result. Permit only minimal fixes in
the implementation and test allowlists; stop on baseline conflicts, forbidden
paths, or broader required changes.

After implementation and debugging, the root orchestrator runs
`python -m backend.app.tooling.agent_task_finalize --task <task> --json` and
passes that report to `spec_reviewer`.

## 8. Review independently

Give `spec_reviewer`:

- the final task package;
- the complete baseline and pre-existing dirty-file inventory;
- the programmer report;
- the debugger report and command results;
- the finalizer report.

Require exactly:

```text
VERDICT: PASS | FAIL
TASK_ID: T\d{3}[A-Z]?
BLOCKING_ISSUES:
NON_BLOCKING_ISSUES:
MISSING_TESTS:
SCOPE_DRIFT:
BASELINE_CONFLICTS:
EXACT_FIX_INSTRUCTIONS:
SAFE_TO_CLOSE: yes | no
```

Accept `SAFE_TO_CLOSE: yes` only with `VERDICT: PASS`.

## 10. Bound repair handling

On `FAIL`, return the verdict to `spec_manager` for classification:

- route missing or incorrect implementation to `PROGRAMMER_ROUTE`;
- route reproducible validation defects or minimal diagnosed fixes to `spec_debugger`.

Keep repairs inside the original package, serialize write-capable roles, rerun the package validation needed for changed code, and invoke `spec_reviewer` again. Count each FAIL handling pass as a repair cycle. Allow no more than two repair cycles. When the second review failure is reached, stop without another repair, do not invoke closer, do not edit `tasks.md`, and report blockers plus the latest validation results.

## 11. Close only after PASS

Invoke `spec_closer` only when the reviewer returned both:

```text
VERDICT: PASS
SAFE_TO_CLOSE: yes
```

Give closer only the exact task ID, the correct `tasks.md` path, the complete reviewer result, and baseline information for that `tasks.md`. Require closer to change exactly one matching checkbox from `[ ]` to `[X]` without changing task text or any other row.

After closer finishes, invoke `spec_manager` for a read-only verification and final summary confirming that only the expected checkbox token changed, no other task was marked, task text stayed identical, and no next task started. If verification fails, report failure; do not attempt unrelated repair.

## 12. Report and stop

End with:

```text
TASK_ID:
FINAL_STATUS: COMPLETED | BLOCKED | FAILED
REVIEW_VERDICT:
FILES_CHANGED:
VALIDATION:
TASKS_MD_CHANGE:
PRE_EXISTING_CHANGES:
REPAIR_CYCLES_USED:
SAFE_TO_COMMIT:
NEXT_TASK_STARTED: no
```

Set `SAFE_TO_COMMIT` from evidence only; it is advisory and never authorizes an automatic commit. Never commit, push, merge, force-push, release, or deploy. A new task always requires a new explicit `$speckit-loop next` or `$speckit-loop T\d{3}[A-Z]?` invocation.

---
name: "speckit-loop"
description: "Run one dependency-ready Spec Kit implementation task through the repository's manager-gated multi-agent workflow. Use when Codex should select the next ready task or execute one explicit T### task with baseline isolation, bounded files, validation, independent review, and closer-only bookkeeping."
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
- an exact uppercase identifier matching `T###`: select that task only.

For any other input, stop without edits and show:

```text
Usage:
  $speckit-loop next
  $speckit-loop T006
```

Never reinterpret free text as a task selector. Never continue to another task after the selected task completes, blocks, or fails.

## 2. Resolve the feature context

From the repository root, run the configured PowerShell prerequisite script:

```powershell
.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks
```

Use another repository-provided variant only if this configured script is unavailable. Parse `FEATURE_DIR` and `AVAILABLE_DOCS`; require absolute resolved paths and stop if `spec.md`, `plan.md`, or `tasks.md` is missing.

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

## 4. Validate the active epic and branch guard

Before capturing the baseline or invoking any agent, run:

```powershell
python -m backend.app.tooling.workstream_validation --guard <selector>
python -m backend.app.tooling.repository_checks --mode preflight
```

The guard reads `.specify/runtime/active-epic`, the matching manifest under
`.specify/workstreams/`, and the current branch. It must stop when the active
epic or manifest is missing, the epic is not `active`, the branch is `master`
or `main`, the branch does not match the manifest, a dependency is not
`completed`, or the selected task is outside the active epic. Errors must show
the active epic, expected branch, current branch, selector, exact reason, and
a safe next step.

The guard is read-only: it never creates or switches branches, commits,
pushes, changes manifests, or writes runtime state. A successful guard is
required before baseline capture. `next` may consider only unchecked tasks in
the active epic; an explicit `T###` must belong to that epic. The one-task
per-run and closer-only completion rules remain unchanged.

Use repository-provided validation modules for mechanical checks. Agents MUST
NOT build semicolon-chained PowerShell validation commands. Run each external
command separately with a finite timeout; a timeout is a structured failure and
must not trigger an indefinite wait or automatic retry.

## 5. Capture the pre-loop baseline

Before invoking the first agent, run and retain the exact output of:

```text
git status --short
git diff --name-only
git diff --cached --name-only
```

Also expand untracked directories with `git ls-files --others --exclude-standard`. Store the baseline in the root thread and classify paths as:

- tracked unstaged modifications, deletions, or renames;
- staged changes;
- untracked files.

Preserve raw command output and the expanded path inventory. Earlier unrelated changes do not fail the loop by themselves. Include them in every manager and reviewer handoff. If implementation or validation needs a path that was dirty at baseline, stop before implementation. No agent may overwrite, absorb, normalize, revert, or claim ownership of that path.

## 6. Select and explore one task

Invoke only direct configured subagents; `max_depth = 1` means subagents must not spawn other subagents.

Run this sequence serially:

```text
spec_manager
  -> spec_explorer
  -> spec_manager
  -> spec_programmer
  -> spec_debugger
  -> spec_reviewer
```

Do not run two `workspace-write` roles concurrently.

### Manager selection pass

Give `spec_manager` the selector, feature context, complete task queue, and baseline.

For `next`, require the manager to:

1. enumerate unchecked tasks;
2. evaluate declared dependencies;
3. verify those dependencies against actual code and tests;
4. nominate exactly one ready task without relying on the lowest ID alone.

For an explicit `T###`, require the manager to verify that the task exists, remains unchecked, and has satisfied declared and actual dependencies. Stop if any check fails.

### Explorer pass

Give `spec_explorer` the nominated task, feature context, and baseline. Require concrete path and symbol evidence for readiness, existing implementation, tests, validation commands, minimal file allowlists, and baseline conflicts. The explorer must not edit.

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
```

Make implementation and test allowlists exact and minimal. List the selected `tasks.md` only under `ALLOWED_BOOKKEEPING_FILES`, and reserve it exclusively for `spec_closer`. It must remain forbidden to programmer and debugger. Stop if an allowed implementation or test path conflicts with the baseline.

## 7. Implement the package

Give `spec_programmer` only the final package plus the relevant `$speckit-implement` instruction.

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

## 8. Debug and validate

Give `spec_debugger` the package and programmer report. Run only package-approved commands, task-focused first and broader checks second. Commands may include, when supported by the repository and selected by the manager:

```powershell
python -m pytest <task-focused-tests>
python -m pytest
python -m compileall backend/app backend/tests
git diff --check
```

Do not require `ruff` unless repository configuration proves it is available. Do not make real provider calls or network requests. Require the debugger to report every command, exit status, and result. Permit only minimal fixes in the implementation and test allowlists; stop on baseline conflicts, forbidden paths, or broader required changes.

## 9. Review independently

Give `spec_reviewer`:

- the final task package;
- the complete baseline and pre-existing dirty-file inventory;
- the programmer report;
- the debugger report and command results;
- the current tracked, staged, and untracked task diff relative to baseline.

Require exactly:

```text
VERDICT: PASS | FAIL
TASK_ID: T###
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

- route missing or incorrect implementation to `spec_programmer`;
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

Set `SAFE_TO_COMMIT` from evidence only; it is advisory and never authorizes an automatic commit. Never commit, push, merge, force-push, release, or deploy. A new task always requires a new explicit `$speckit-loop next` or `$speckit-loop T###` invocation.

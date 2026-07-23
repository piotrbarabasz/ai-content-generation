---
name: "speckit-implement"
description: "Implement exactly one complete, manager-approved Spec Kit task package within explicit file allowlists. Use when a programmer agent receives a bounded package from $speckit-loop and must make the smallest defensible code or test change without queue progression or task bookkeeping."
metadata:
  author: "ai-content-generation"
  source: "project single-task implementation workflow"
---


## User Input

```text
$ARGUMENTS
```

Treat `$ARGUMENTS` and the current handoff context as one candidate task package. Implement exactly one selected task package and then stop.

## Required task package

Require every field below before editing:

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

Stop without edits when a field is missing or ambiguous, more than one task is included, `TASK_ID` does not match `T\d{3}[A-Z]?`, or the package is not manager-approved.

## Preflight

Before editing:

1. Read the package's relevant artifact references and inspect the existing implementation and tests for the selected task.
2. Treat `ALLOWED_IMPLEMENTATION_FILES` and `ALLOWED_TEST_FILES` as exact write allowlists, not examples.
3. Treat `FORBIDDEN_FILES`, Spec Kit artifacts, and bookkeeping files as non-writable.
4. Confirm that neither an allowed implementation file nor an allowed test file appears in `PRE_EXISTING_DIRTY_FILES`.
5. Compare the current repository state with `BASELINE_STATUS`; stop if a required path has acquired an unexplained conflict.
6. Confirm that the requested change can satisfy the acceptance criteria without expanding the package.

Report the blocking condition and stop if any check fails. Never overwrite, revert, absorb, normalize, or take ownership of a pre-existing change.

## Implementation rules

- Make the smallest defensive change that satisfies the package.
- Preserve repository conventions and every listed architecture invariant.
- Write only to `ALLOWED_IMPLEMENTATION_FILES` and `ALLOWED_TEST_FILES`.
- Do not create a new path unless it is explicitly present in the appropriate allowlist.
- Stop and report the needed path or scope when implementation requires anything outside the allowlists.
- Do not perform broad refactors, unrelated cleanup, speculative improvements, or fixes for baseline problems.
- Do not create or update ignore files unless the selected package explicitly names the exact ignore file and requires that change.
- Do not edit `tasks.md`, task text, checkboxes, `spec.md`, `plan.md`, `data-model.md`, contracts, research, quickstart, the constitution, or another Spec Kit artifact.
- Do not make real provider connections or network calls in tests.
- Do not commit, push, merge, force-push, release, or deploy.
- Do not select, implement, or begin another task.
- Do not run repository mechanical validation modules or raw Git inventory commands.
- Run only task-focused tests explicitly listed in `VALIDATION_COMMANDS`.

## Validation

Use repository-provided validation modules only when the package explicitly
lists them as task-focused checks. Do not build semicolon-chained PowerShell
validation commands. Every external command MUST have a finite timeout. A
timeout MUST produce a structured failure and MUST NOT trigger automatic
retries.

Run only applicable task-focused commands from `VALIDATION_COMMANDS`. Do not
invent an unconfigured linter or broaden validation beyond the package. Record
each exact command, exit status, and result. A failing command may be
diagnosed, but any fix must remain inside the same allowlists and acceptance
criteria.

Stop when a fix needs wider scope, a forbidden path, or a baseline-dirty path. Leave systematic post-implementation validation to `spec_debugger` when the handoff requires it.

## Completion report

Return exactly these sections:

```text
TASK_ID:
CHANGED_FILES:
IMPLEMENTATION_SUMMARY:
COMMANDS_RUN:
VALIDATION_RESULTS:
UNRESOLVED_ISSUES:
READY_FOR_DEBUGGER: yes | no
READY_FOR_REVIEWER: yes | no
```

Readiness means only that the bounded implementation can proceed to the named role. It does not complete bookkeeping and does not authorize changes to `tasks.md`. Stop after the report.

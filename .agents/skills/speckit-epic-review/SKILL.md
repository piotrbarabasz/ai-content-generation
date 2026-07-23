---
name: speckit-epic-review
description: Perform a read-only, baseline-aware review of the active Spec Kit epic before PR creation, including task evidence, branch diff, required checks, architecture invariants, security, scope drift, and cross-task consistency.
---

# Spec Kit Epic Review

Run `$speckit-epic-review` from the repository root. Review exactly the epic
selected by `.specify/runtime/active-epic`. This skill is read-only and must not
modify code, tasks, manifests, runtime state, or Git history.

Before any Python command, resolve the pinned interpreter with:

```powershell
$pythonBin = git config --local --get agent.python
if (-not $pythonBin) {
    throw "agent.python is not configured. Run scripts/setup-dev.ps1 to pin the repository interpreter."
}
if (-not (Test-Path -LiteralPath $pythonBin -PathType Leaf)) {
    throw "agent.python points to a missing interpreter: $pythonBin"
}
& $pythonBin -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
```

Use `$pythonBin` for every Python invocation in this workflow. Never fall back to bare `python`.

## Workflow

1. Read `.specify/runtime/active-epic`. Stop with a blocking finding if it is
   missing or empty.
2. Read the active epic manifest from `.specify/workstreams/` and its milestone
   manifest. Consume the task finalize reports produced during the loop and
   include report failures in `BLOCKING_ISSUES` while continuing to collect
   other read-only evidence when safe. If implementation or validation changed
   after a finalize report was captured, require a fresh finalize run before
   trusting it.
3. Read the active feature artifacts under the epic's `feature` directory:
   `spec.md`, `plan.md`, `tasks.md`, available data model/contracts/research/
   quickstart documents, and `.specify/memory/constitution.md`.
4. Read the current branch and compare it with the epic `branch`. Read the
   local base branch without changing branches. Stop or mark FAIL when the
   current branch is wrong, is `master`/`main`, or the base branch is absent.
5. Inspect, without changing anything. Use the review receipt, the task
   finalize reports, and bounded per-file inspection when patch content is
   needed. Do not request one full diff for the entire epic. Every external
   command MUST have a finite timeout. A timeout MUST produce a structured
   failure and MUST NOT trigger automatic retries.

   If the repository is dirty, distinguish pre-existing changes from epic
   changes and report relevant baseline conflicts. Never stash, reset, stage,
   checkout, commit, merge, fetch, pull, push, or rebase.
6. Parse every task listed by the epic. Task IDs use `T\d{3}[A-Z]?` and must
   be preserved exactly. Verify its checkbox, dependencies,
   implementation/test files, acceptance criteria, and actual code/test
   evidence. Do not treat a checked checkbox as proof of implementation.
7. Review the milestone completion criteria, epic criteria, constitution
   architecture invariants, and required checks. Check that tasks cooperate,
   domain models and workflow contracts remain compatible, abstractions are
   not duplicated, and changes stay within the epic.
8. Run only the exact commands in `required_checks`. Execute them locally and
   deterministically. Do not run network commands, real providers, or any
   command not listed by the manifest. This is the only loop stage that may
   execute a manifest-required full `& $pythonBin -m pytest` if the manifest asks
   for it. Record command, exit code, and result.
9. Inspect tests for behavioral assertions rather than import-only coverage.
   Check for secrets, credentials, generated outputs, runtime artifacts, and
   other sensitive material in the epic diff.
10. Produce the required report. Include `HEAD_SHA`, `BASE_SHA`, and
    `REVIEW_RECEIPT_WRITTEN`. `SAFE_TO_CREATE_PR: yes` is permitted only with
    `VERDICT: PASS`. Do not create a PR automatically. When the review passes,
    the root orchestrator records `.specify/runtime/reviews/<EPIC_ID>.json`
    by invoking `& $pythonBin -m backend.app.tooling.epic_review_receipt write
    --epic <EPIC_ID> --review-json <path>` before any PR or merge step.

## Review rules

- All epic tasks must be checked off and supported by code/test evidence.
- Missing implementation, missing behavioral tests, unmet acceptance criteria,
  failed required checks, dependency inconsistencies, security findings, or
  scope drift are blocking unless explicitly shown to be pre-existing and
  unrelated.
- Preserve the distinction between baseline changes and epic changes.
- `FINAL_LLM_REVIEW_REQUIRED` is always `yes`; the report is advisory and does
  not authorize PR creation, merge, deployment, commit, or push.

## Required report

Return exactly these fields:

```text
EPIC_ID:
MILESTONE_ID:
BRANCH:
BASE_BRANCH:
HEAD_SHA:
BASE_SHA:
REVIEW_RECEIPT_WRITTEN:
TASKS_COMPLETE:
REQUIRED_CHECKS:
CHANGED_FILES:
BLOCKING_ISSUES:
NON_BLOCKING_ISSUES:
MISSING_TESTS:
CROSS_TASK_INCONSISTENCIES:
SCOPE_DRIFT:
SECURITY_FINDINGS:
PR_SUMMARY:
PR_TEST_PLAN:
VERDICT: PASS | FAIL
SAFE_TO_CREATE_PR: yes | no
FINAL_LLM_REVIEW_REQUIRED: yes | no
```

Use `SAFE_TO_CREATE_PR: no` for every `FAIL` and whenever required evidence is
missing. End after this one epic review; do not start a task loop.

---
name: speckit-epic-close
description: Perform bookkeeping-only closure of one Spec Kit epic after independently verifying its merged PR, completed tasks, and inclusion in the base branch history.
---

# Spec Kit Epic Close

Run `$speckit-epic-close E###` from the repository root. This workflow is
bookkeeping only and must not merge, close a PR, delete a branch, modify tasks,
or perform unrelated changes.

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

## Preconditions

1. Accept only an exact uppercase `E###` argument. Otherwise stop with:

   ```text
   Usage:
     $speckit-epic-close E001
   ```
2. Read `.specify/runtime/active-epic`, the selected epic manifest, and its
   milestone manifest. Require the runtime selector to match the selected epic.
3. Require epic status `active` or `review`, every listed task checked, and
   task evidence consistent with the epic. Never modify task checkboxes.
4. If a review receipt exists at `.specify/runtime/reviews/<EPIC_ID>.json`,
   remove only the selected epic's receipt after the close preconditions pass
   by invoking `& $pythonBin -m backend.app.tooling.epic_review_receipt delete
   --epic <EPIC_ID>`.

## Merge evidence

Do not trust a user statement that the PR was merged. Prefer authoritative PR
metadata when a configured GitHub integration is available and confirm the PR
exists, is merged, matches the epic branch and base branch, and includes either
`mergedAt` or merge commit metadata. This is the preferred proof for squash
merge, merge commit, and rebase merge histories.

Use `& $pythonBin -m backend.app.tooling.epic_close_evidence --epic <EPIC_ID>
--json` to evaluate merge evidence deterministically. When GitHub metadata is
unavailable, local ancestry can only support merge commit and fast-forward
evidence; it cannot prove squash merge or a typical rebase merge. For squash
or rebase, require GitHub metadata or another authoritative artifact.

If GitHub metadata is unavailable, fall back to local ancestry only when the
epic HEAD is demonstrably part of the base branch history:

```powershell
git branch --show-current
git merge-base --is-ancestor <epic_head> <base_branch>
git log --oneline --first-parent <base_branch>
```

Local ancestry is sufficient for merge commit and fast-forward histories, but
it cannot prove squash merge or rebase merge on its own. A branch merely
existing, a PR being closed, or a local diff being empty is not merge evidence.
If the evidence is unavailable or contradictory, stop without edits.

## Bookkeeping procedure

After all preconditions pass:

1. Change only the selected epic manifest `status` from `active` or `review` to
   `completed`.
2. Evaluate the milestone. Change its status only when every listed epic has
   status `completed` and every `completion_criteria` item is supported by
   evidence. Otherwise leave it unchanged.
3. Show the exact manifest diff and verify no task or unrelated file changed.
4. Remove `.specify/runtime/active-epic` only when it contains the selected
   epic ID. Do not remove the runtime directory or any other runtime file.
5. Never delete the local or remote branch automatically. Never close or merge
   the PR. Never push, fetch, pull, rebase, or change branch protection.
6. Never remove review receipts for any other epic.

Use the existing `validate_close_preconditions` helper where practical. The
close operation remains root-orchestrated and deterministic; no new agent is
needed for bookkeeping.

## Required report

Return exactly:

```text
EPIC_ID:
EPIC_STATUS:
MILESTONE_ID:
MILESTONE_STATUS:
MERGE_EVIDENCE:
MANIFEST_FILES_CHANGED:
ACTIVE_EPIC_CLEARED:
BRANCH_DELETED: no
```

If blocked, report the exact reason and safe next step in `MERGE_EVIDENCE`,
leave manifests and runtime state unchanged, and set
`MANIFEST_FILES_CHANGED: none` and `ACTIVE_EPIC_CLEARED: no.

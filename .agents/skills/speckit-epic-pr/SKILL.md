---
name: speckit-epic-pr
description: Prepare and, only when every safety gate passes, create a draft pull request for the active reviewed Spec Kit epic without pushing, merging, enabling auto-merge, or changing branch protection.
---

# Spec Kit Epic PR

Run `$speckit-epic-pr` from the repository root. This workflow handles one
active epic and never performs merge, push, deployment, or automatic epic status
updates. It may create only a draft PR, and only when the required evidence is
already available.

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

## Mandatory gates

Read `.specify/runtime/active-epic`, the active epic manifest, its milestone
manifest, and the latest `$speckit-epic-review` result. Stop before any PR API
call unless all of these are true:

- an active epic exists and its manifest is valid;
- the current branch matches the epic `branch` and is not `master` or `main`;
- every epic task is checked and has implementation/test evidence;
- the review contains `VERDICT: PASS` and `SAFE_TO_CREATE_PR: yes`;
- a review receipt exists at `.specify/runtime/reviews/<EPIC_ID>.json` and
  matches the active epic ID, milestone ID, branch, base branch, current HEAD
  SHA, current base SHA, verdict, safe flag, and required checks;
- the local head has at least one commit relative to `base_branch`;
- the diff contains no secrets, credentials, ignored runtime state, generated
  outputs, or other runtime artifacts.

The review result must correspond to the current head and active epic. A stale,
missing, ambiguous, or mismatched review is a blocking condition. Do not infer
PASS from tests or checkboxes.

## Workflow

1. Read the manifest fields `id`, `milestone`, `feature`, `base_branch`,
   `branch`, `tasks`, `required_checks`, `pr_policy`, and `commit_policy`.
2. Verify the active epic and branch, then inspect the remote configuration:

   ```powershell
   git remote -v
   git branch --show-current
   & $pythonBin -m backend.app.tooling.epic_review_receipt validate --epic <EPIC_ID>
   ```

   Do not request one full patch for the entire epic. When patch content is
   needed, inspect it only per file or for a small, explicit list of files.
   Keep the inspection bounded and read-only.

   Use these bounded local inspection commands before requesting any patch
   content:

   ```powershell
   git --no-pager diff --name-only <base_branch>...<epic_branch>
   git --no-pager diff --stat <base_branch>...<epic_branch>
   git --no-pager log --oneline <base_branch>..<epic_branch>

   Use the resulting file list and summary to select only the specific files
   that require per-file inspection. Never request one full epic-wide patch.

   Use read-only remote inspection to determine whether `<epic_branch>` exists
   remotely and whether a PR already exists. Do not fetch, pull, push, or alter
   remote configuration.
3. Re-read `HEAD` and `<base_branch>` with separate `git rev-parse` commands
   and compare them with the review receipt before trusting the receipt.
4. If the branch is not on the remote, stop and print the exact command a human
   may run, for example:

   ```text
   git push -u origin <epic_branch>
   ```

   Do not execute it and set `SAFE_TO_CREATE_PR: no`.
5. If the branch exists remotely, check whether a PR already exists. If it does,
   report its number and URL and do not create another PR.
6. If no PR exists and the GitHub/PR integration is safely available, create
   only a draft PR with the prepared title and body. Do not mark it ready,
   merge it, enable auto-merge, change branch protection, push, or update the
   epic manifest.
7. If the environment cannot safely create a PR, return the complete title and
   body for manual use. Never claim a PR number or URL that was not returned by
   the integration.

## PR content

Prepare:

- title: concise epic title, including the epic ID;
- body: summary, task list, acceptance outcome, risks, required-check results,
  test plan, final LLM review requirement, and manual merge checklist.

The manual merge checklist must state that a human must verify the draft, final
LLM review, branch protection checks, task completion, and scope before merging.
It must explicitly say that merge and deploy are not automated by this skill.

## Required report

Return exactly:

```text
PR_NUMBER:
PR_URL:
HEAD:
BASE:
DRAFT_STATUS:
REQUIRED_FINAL_ACTIONS:
```

For a blocked or manual-preparation result, use `PR_NUMBER: none` and
`PR_URL: none`, then include the exact blocking reason, safe next action, and
ready-to-copy title/body in `REQUIRED_FINAL_ACTIONS`.

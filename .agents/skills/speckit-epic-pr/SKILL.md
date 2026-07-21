---
name: speckit-epic-pr
description: Prepare and, only when every safety gate passes, create a draft pull request for the active reviewed Spec Kit epic without pushing, merging, enabling auto-merge, or changing branch protection.
---

# Spec Kit Epic PR

Run `$speckit-epic-pr` from the repository root. This workflow handles one
active epic and never performs merge, push, deployment, or automatic epic status
updates. It may create only a draft PR, and only when the required evidence is
already available.

## Mandatory gates

Read `.specify/runtime/active-epic`, the active epic manifest, its milestone
manifest, and the latest `$speckit-epic-review` result. Stop before any PR API
call unless all of these are true:

- an active epic exists and its manifest is valid;
- the current branch matches the epic `branch` and is not `master` or `main`;
- `git status --short`, `git diff --name-only`, and
  `git diff --cached --name-only` are empty;
- every epic task is checked and has implementation/test evidence;
- the review contains `VERDICT: PASS` and `SAFE_TO_CREATE_PR: yes`;
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
   git status --short
   git log --oneline <base_branch>..<epic_branch>
   git diff --name-only <base_branch>...<epic_branch>
   git diff <base_branch>...<epic_branch>
   ```

   Use read-only remote inspection to determine whether `<epic_branch>` exists
   remotely and whether a PR already exists. Do not fetch, pull, push, or alter
   remote configuration.
3. If the branch is not on the remote, stop and print the exact command a human
   may run, for example:

   ```text
   git push -u origin <epic_branch>
   ```

   Do not execute it and set `SAFE_TO_CREATE_PR: no`.
4. If the branch exists remotely, check whether a PR already exists. If it does,
   report its number and URL and do not create another PR.
5. If no PR exists and the GitHub/PR integration is safely available, create
   only a draft PR with the prepared title and body. Do not mark it ready,
   merge it, enable auto-merge, change branch protection, push, or update the
   epic manifest.
6. If the environment cannot safely create a PR, return the complete title and
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

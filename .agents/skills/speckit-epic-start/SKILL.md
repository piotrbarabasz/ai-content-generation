---
name: "speckit-epic-start"
description: "Prepare one local epic workstream by validating its manifests, creating or selecting its branch, and writing ignored active-epic runtime state."
metadata:
  author: "ai-content-generation"
  source: "project workstream workflow"
---

## User input

```text
$ARGUMENTS
```

Prepare exactly one epic. Accept only an exact uppercase identifier matching
`E###`. For any other input, stop without edits and show:

```text
Usage:
  $speckit-epic-start E001
```

## Root-orchestrator rule

This skill is executed by the root orchestrator. Do not invoke a new agent for
Git operations and do not ask a model to make broad repository changes. Git
commands are deterministic and must be run by the root orchestrator in the
repository root.

## Preparation sequence

1. Resolve the repository root and read:
   - `.specify/workstreams/E###-*.yml`;
   - the milestone manifest named by the epic's `milestone` field;
   - `.specify/workstreams/schema.md`.
2. Run the repository workstream validation:

   ```powershell
   python -m backend.app.tooling.workstream_validation
   ```

   Stop if validation fails.
3. Require the epic status to be exactly `active`. If it is not active, stop
   and explain that a human must update the manifest; never change the
   manifest automatically.
4. Verify the epic is listed by the milestone and that the milestone points
   back to the epic.
5. Check every `depends_on` epic. Each dependency must exist and have status
   `completed`; otherwise stop before any Git or runtime-state write.
6. Capture and retain all working-tree evidence:

   ```powershell
   git status --short
   git diff --name-only
   git diff --cached --name-only
   git ls-files --others --exclude-standard
   ```

   If any tracked modification, staged change, deletion, rename, or
   non-ignored untracked path exists, stop and show the exact paths. Do not
   reset, stash, overwrite, normalize, stage, or incorporate those changes.
7. Read the current branch with:

   ```powershell
   git branch --show-current
   ```

8. Verify that `base_branch` exists locally:

   ```powershell
   git show-ref --verify --quiet refs/heads/<base_branch>
   ```

   Stop if it does not. Do not fetch or pull.
9. If the epic branch does not exist locally, create and check it out from the
   local base branch:

   ```powershell
   git switch -c <epic_branch> <base_branch>
   ```

   If it already exists, never reset it. Switch to it only after the clean
   working-tree check:

   ```powershell
   git switch <epic_branch>
   ```

   Never perform pull, fetch, rebase, merge, push, commit, force-push, or PR
   operations.
10. Create the ignored runtime directory if needed and write only the epic ID
    with UTF-8 text to:

    ```text
    .specify/runtime/active-epic
    ```

    Do not write branch state, timestamps, task state, or other runtime data.

## Completion report

Report exactly:

```text
EPIC_ID:
MILESTONE_ID:
BASE_BRANCH:
EPIC_BRANCH:
CURRENT_BRANCH:
TASKS:
DEPENDENCIES:
ACTIVE_EPIC_WRITTEN: yes | no
SAFE_TO_RUN_LOOP: yes | no
```

`SAFE_TO_RUN_LOOP: yes` is allowed only when the manifest validation passed,
the epic is active, all dependencies are completed, the branch matches the
manifest, and the runtime file was written successfully. A successful start
does not authorize the single-task loop to skip its own branch guard, baseline,
review, or closer-only completion rules.

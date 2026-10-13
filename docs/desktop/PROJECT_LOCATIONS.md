# Project storage cleanup

Scope: desktop project creation, local workspace relocation and Git exclusion.
Application/storage APIs continue accepting explicit roots for recovery, imports
and isolated tests. Provider caches, test fixtures and backups are not projects
to relocate automatically.

## Creation and reopening

Source desktop runs create `projects/<sanitized-project-name>/` relative to the
checkout, even when launched from another working directory. Installed builds
use `%LOCALAPPDATA%/AI Content Studio/projects/`, outside the installation.
Names cannot traverse directories or use Windows device aliases. An existing
directory is rejected instead of reused. Open starts in the projects directory
and still supports older workspaces elsewhere.

`/projects/` is ignored. Previously tracked project databases and generated media
are removed from the Git index only; local files are preserved. Historical Git
commits still contain the formerly tracked data.

## Local relocation performed on 2026-10-13

The following checkout-root directories were relocated under `projects/`:

- `acceptance-social-001`
- `smoke-e2e-english-001`
- `test-gps`
- `test-gps-en`
- `test-gps-en-2`
- `gps-einstain`

Root-level `artifacts/` and `cache/`, without an accompanying project database,
were preserved under `projects/legacy-root-data/`. No missing database or script
was reconstructed. The existing `projects/acceptance-standard-10min-001` was
preserved, including its locally modified jobs database and work files.
`accept_scenes.py` now resolves its relocated workspace from the checkout.

Before relocation, read-only exclusive transactions checked that discovered
SQLite databases were not busy. SHA-256 inventories before and after relocation
verified all 463 moved files and all 537 existing project files byte-for-byte.
Neither SQLite rows nor artifact identities were edited. Windows refused some
directory renames; their files were relocated individually, leaving empty
directory shells in the original locations. Local verification manifests are
under ignored `.tmp/`; user project contents are not included in this document.

## Regression checks

Location tests cover launch-directory independence, writable installed storage,
safe names and existing-data protection. The offline Qt regression exercises
Create, refusal to reuse an existing workspace and reopening the same identity.

Validation: 27 focused location/editor tests passed. Full backend suite:
2328 passed, 11 skipped in 1733 seconds. Both working-tree and staged
`git diff --check` passed. No project data is included in the cleanup commit.

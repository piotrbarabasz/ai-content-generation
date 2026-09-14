# D044 — Workspace path containment

## Implementation boundary

The D004 immutable store is the publication/read boundary. Validate portable
relative keys before filesystem access; bind configured roots and reject links or
Windows reparse points below them at each I/O boundary. Catalog ownership lookup
precedes resolving an artifact's key. Keep legacy key access and add opaque ID
access scoped to the store's catalog.

Explicit caller-selected external files remain supported by `import_file` (D004).
Its optional `source_root` confines relative or absolute selections to that root;
artifact IDs are never interpreted as external file selections. Reject ADS,
device names, drive-relative paths and redirected import sources.

Include private publication journals, SQLite databases and their journal paths,
and the existing audio worker/FFmpeg file boundaries: these are alternate access
paths to project media. No database schema or provider contract change is needed.
FFmpeg receives separate absolute path arguments with no shell interpretation.
The private worker source closure must ship this standard-library-only resolver;
otherwise the isolated interpreter cannot import its audio operations.

This task does not implement D016's image selection UI, a filesystem browser, or
a hostile-process sandbox. Checks detect redirection present at an operation
boundary, including changes during stream transfer; they do not provide OS-handle
isolation against a concurrent process racing a check and a filesystem call.
Configured roots are explicitly trusted selections; hard-link aliases and a
malicious process modifying already-open files are not isolated by this policy.

## Acceptance evidence

| Criterion | Evidence | Result |
| --- | --- | --- |
| Traversal/redirected destinations cannot escape the workspace | Reject traversal, drives, device names, ADS and reparse points; recheck publication after stream transfer; quarantine redirected recovery stages; protect SQLite and audio chunk paths. Outside sentinels remain unchanged. | PASS |
| Unicode and space paths work | Import from a Unicode/space source into a Unicode workspace; read through an opaque ID; FFmpeg fixture receives absolute paths containing Unicode, spaces and shell metacharacters as individual arguments. | PASS |
| Unknown IDs do not probe arbitrary files | Unknown/traversal-shaped and foreign-project IDs fail catalog lookup before artifact path resolution; legacy unknown storage keys follow the same rule. | PASS |

## Validation

Windows, isolated `.venv-ci311`, Python 3.11.9, 2026-09-14. All fixtures are offline;
no real model, provider or FFmpeg invocation is needed for the new tests.

- Baseline D004 streaming/publication tests: 25 passed.
- Final focused suite: **141 passed, 11 skipped** in 17.93 s.
- Full `python -m pytest backend/tests`: **1151 passed, 11 skipped** in 115.47 s.
- `git diff --check`, UTF-8/whitespace and task-status scope checks: PASS.
- Native junctions and NTFS ADS: exercised successfully. Eleven real symlink
  cases are skipped because Windows denies creation with `WinError 1314`.
  Re-run these on Windows with symlink privilege/Developer Mode before relying
  on that platform capability. POSIX execution was not available this session.
- The first full run found a missing resolver in the private worker bundle and
  a changed empty-key error message. Both were corrected; focused tests now
  include isolated worker imports and the existing error contract.

Focused command (using the isolated interpreter):

```text
python -m pytest backend/tests/unit/test_workspace_containment.py backend/tests/unit/test_streaming_artifacts.py backend/tests/unit/test_artifact_store.py backend/tests/unit/test_t009.py backend/tests/unit/test_section_voice.py backend/tests/integration/test_artifact_publication.py backend/tests/integration/test_section_audio.py backend/tests/integration/test_section_tempo.py backend/tests/integration/test_speech_boundary.py -ra
```

## Changed files

- `backend/app/storage/paths.py`: shared portable key, root, import and SQLite checks.
- `backend/app/storage/local_store.py`: guarded publication/recovery/read and opaque ID lookup.
- `backend/app/storage/artifact_index.py`: revalidate the owning project's index paths.
- `backend/app/storage/project_repository.py`: guard project database and journals.
- `backend/app/jobs/repository.py`: guard queue connections and publication attachment.
- `backend/app/storage/section_tempo.py`: guard the configured tempo workspace.
- `backend/app/runtime/section_synthesis.py`: guard worker inputs and output evidence.
- `backend/app/runtime/worker_bundle.py`: ship the resolver into the isolated worker.
- `backend/app/tts/chunk_synthesis.py`: guard chunk reuse, cleanup and publication paths.
- `backend/app/tts/manifest.py`: guard manifest I/O and relative audio references.
- `backend/app/tts/post_processing.py`: guarded absolute FFmpeg paths and output reads.
- `backend/tests/unit/test_workspace_containment.py`: portable and native Windows boundary tests.
- `backend/tests/integration/test_section_audio.py`: adversarial worker media references/junctions.
- `docs/desktop/D044_WORKSPACE_CONTAINMENT.md`: task boundary, acceptance and validation evidence.
- `docs/desktop/IMPLEMENTATION_PLAN.md`: only D044 completion/evidence.

Branch: `feat/d044-workspace-path-containment`. No later task, commit, push or merge.

# API and storage

FastAPI is the optional HTTP adapter in the accepted desktop architecture, not
the primary product interface. The following documents current behavior. The
[desktop plan](../desktop/IMPLEMENTATION_PLAN.md) defines the future shared
application services, SQLite project state and file publication protocol.

`app.api.main:app` is the FastAPI application. Routes use `/api/v1` by default;
`create_app()` builds an instance and `ApiSettings`/`ApiDependencies` provide
configuration and service injection. OpenAPI is the current HTTP schema reference.

| Route group | Current behavior |
| --- | --- |
| Projects and workflow configs | Validates and creates in-memory domain records; selection uses the canonical TTS mapper |
| Workflow runs | Creates/reads status records; start and resume do not execute modules |
| Artifacts | Lists registered metadata; there is no general artifact-bytes download endpoint |
| Export bundle | Creates/returns bundle metadata; does not run `ExportModule` or materialize its required files |
| Approvals | Lists and mutates explicitly registered in-memory checkpoints; unresolved checkpoints block resume |
| TTS catalog | Filtered, deterministic discovery without loading models |
| TTS previews | Validated synthesis service, cached metadata and WAV delivery by opaque preview ID |
| Publishing/localization | Reads and updates explicitly registered in-memory handoffs; no HTTP upload operation |

The API dictionaries are process-local and are lost on restart. They are not a
database or a multi-worker-safe repository. Some current integration tests call
route functions directly; newer TTS tests also exercise the ASGI request boundary.

## Editable project persistence (D003)

`app.application.projects.ProjectSession` provides create/open, section editing,
reorder and retained-revision selection through injected repository/factory ports.
It has no SQLite, Qt, HTTP or provider imports. Composition supplies
`app.storage.project_repository.ProjectRepository`; this repository does not
replace or persist the existing API dictionaries.

Each local workspace contains `project.sqlite` with schema `user_version = 1`
and application ID `0x41494353` (AICS). The single project row stores a snapshot
of the existing `Project` metadata, a relative workspace reference `.` and the
active script revision ID. Normalized section/script identity tables, immutable
revision rows and ordered script-section references retain the D001 model values,
including their parent IDs. Workspace IDs remain opaque identities; absolute
workspace paths are supplied at open time and are not saved in the database.

Saving a script inserts any new section/script revisions and changes the active
pointer in one transaction. A mismatched expected active revision, invalid parent,
foreign project, reused immutable ID with different content or SQLite failure
rolls back the whole operation. There is no in-memory active-state cache to become
inconsistent after rollback. Historical snapshots remain accessible and selectable.

The repository uses SQLite's EXCLUSIVE connection locking with a DELETE rollback
journal and `synchronous = FULL`. Transactions commit independently while the
session retains its lock. A second session fails immediately with
`ProjectWriterBusyError`, including between edits. This minimal format also
excludes separate readers; it is not concurrent multi-client or network-share
storage. Closing the session or terminating its process releases the lock; SQLite
recovers uncommitted journaled changes when the project is reopened. These lock
semantics follow [SQLite's locking-mode documentation](https://www.sqlite.org/pragma.html#pragma_locking_mode).

Creation refuses an existing database. Opening never initializes a missing file,
adopts an unversioned database or migrates an unsupported version/application ID.
Unsupported formats are left unchanged. A failed initial creation can leave an
empty, unversioned file; it is rejected on subsequent open and never silently
reinitialized. Schema migrations are D042.

```python
from app.application.projects import ProjectSession
from app.storage.project_repository import ProjectRepository

# Composition chooses the SQLite adapter; workspace is a caller-supplied Path.
with ProjectSession.create(workspace, repository_factory=ProjectRepository,
                           name="My project", language="en") as session:
    project_id = session.project.id
    draft = session.active_script  # persisted empty draft
    # Save structured ScriptRevision values with an expected active revision ID.

with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
    assert session.project.id == project_id
    assert session.active_script == draft
```

Always close the session before relocating its workspace. Tests exercise reopening
after movement to a Unicode/spaces path, A/B/C history with selected B2, transaction
rollback, actual competing processes and abrupt process exit before/after commit.
This task does not publish media, implement jobs, add undo UI or harden untrusted
filesystem paths; those remain separate desktop tasks.

## Artifact persistence

`ArtifactStore` is the abstract save/read/list interface. `LocalArtifactStore(root)`
creates stored bytes and manifest sidecars under an explicitly supplied root.
Storage keys are relative, normalized and checked against lexical traversal.
Resolved-path containment, including Windows junctions, is future hardening in
the desktop plan; current checks are not a sandbox for untrusted projects. Metadata
includes the owning workflow run, producing module, artifact type, version,
checksum and storage reference through `ArtifactManifest`.

Each generated key includes a unique artifact ID, so repeated friendly names do
not overwrite earlier artifacts. Current save/read methods operate on whole
payloads and sidecar writes are not a transaction with media publication. Artifact
indexing in SQLite, streaming, artifact selection and publication recovery remain
planned work; D003 persists editorial project state only.

`ExportModule` saves a manifest, workflow configuration and run snapshot; it
includes available artifacts/references and explicitly reports missing optional
ones. Platform handoff builders collect validated metadata and checksummed
references. This product behavior remains separate from the provisional HTTP
export implementation until the application service is connected.

## Preview and reference audio

`ApiSettings.tts_preview_root` defaults to `.runtime/tts-previews` and remains
injectable. Previews store relative manifests and WAV files under their own root,
separate from production narration chunks. Cache reads validate both manifest and
WAV integrity. API callers receive opaque IDs and audio URLs, never storage paths.

Reference-voice previews require an injected resolver for approved opaque audio
artifact IDs. The default API dependency does not provide such a resolver or an
upload/approval API. Builtin selections work with configured runtimes; adding
managed reference-audio intake and application resolver wiring is roadmap work.

Prior installations used `.specify/runtime/tts-previews`. Cleanup does not erase
that ignored cache. It may be regenerated; applications requiring those existing
IDs may explicitly configure the former root while arranging a separate data
migration. No automatic data move is performed at import or application startup.

Keep `.runtime`, `.local`, artifacts, outputs, model caches and private reference
audio ignored. Do not add real credentials to configuration DTOs or manifests.

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

### Transactional split and merge (D038)

`SectionEditingService` and the `ProjectSession.split_section`, `merge_sections`
and `describe_section_edit` conveniences reuse `save_and_select`. New section
identities, the child script and active pointer commit together. Exact retained
parent/child snapshots encode source lineage without changing schema version 1;
rollback cannot leave partial identities. Details and validation are in
[D038 split/merge](../desktop/D038_SECTION_SPLIT_MERGE.md).

## Durable local job queue (D006)

`app.jobs.repository.JobRepository` binds `jobs.sqlite` to the live D003 session.
The queue has its own schema version 1 and application ID `0x4149434A` (AICJ);
`project.sqlite` and the artifact index keep their existing schemas. The only
D003 code addition is an ephemeral session ID shared by services using that
repository instance. It distinguishes adapter reconstruction from project restart.

Queue transactions persist immutable job input JSON, separately numbered attempts,
claim tokens, phase/count progress and outcomes. `BEGIN IMMEDIATE` serializes
claim/pause/cancel/finish commands; only a queued attempt can be claimed, and
updates require its current running claim token. Queue access remains on the
project's coordinator thread. The D007 supervisor receives private-pipe events
and applies queue commands on that thread; workers never open the project/queue.

The first queue open in a new exclusively owned project session atomically marks
old running attempts `interrupted`. Pending work, pause state, progress and
terminal history survive. Retries are explicit new attempts over the same input
snapshot. Completion stores reported output references only; media validation,
conditional publication and active selection remain D004/D040 responsibilities.
No queue command edits or removes retained artifact records or files.
See [D006 transitions, recovery and evidence](../desktop/D006_DURABLE_JOBS.md).

## Isolated worker lifecycle (D007)

`runtime.supervisor.WorkerSupervisor.run_next()` is an async operation for one
D006 claim. Its trusted `WorkerLaunch` selects a native executable or a fixed
Python script; job data cannot select commands. The version-1 binary pipe protocol
uses a four-byte length followed by at most 256 KiB of strict UTF-8 JSON, including
job/attempt IDs. Only a matching handshake permits the frozen job snapshot to be
sent. stdout carries frames; stderr is continuously drained into a bounded tail.

Progress and terminal events are applied on the owning coordinator event-loop
thread. Completion requires a valid outcome, EOF and exit zero. Cancellation
first requests cooperation, then enforces termination after a grace period;
timeouts and protocol failures cannot report success. Windows launch is hidden
and a kill-on-close Job Object contains the child before the handshake. Process
exit and pipe drainage precede a terminal queue update. Application composition
must await `close()`/`run_next()` before closing its D003 session.

The base worker handles `diagnostic.echo`. D008's private Piper entrypoint also
handles health and D010 single-section synthesis; selection remains owned by the
coordinator. Source and standalone protocol smoke tests are implemented; Qt
event-loop integration remains later work. See [D007 protocol, lifecycle and evidence](../desktop/D007_WORKER_LIFECYCLE.md).

## Approved runtime profile metadata (D045)

`runtime.profiles` defines the strict v1 manifest and typed health outcomes.
`profile_catalog` exposes one shipped Windows x64 CPU profile with a pinned
embedded CPython, seven wheels and an explicit MSVC native-runtime artifact.
Descriptors are immutable; unknown fields/commands, incompatible wheel tags,
incomplete dependency closure and absent provenance are rejected.

Discovery reads packaged JSON through `importlib.resources` and checks the
curated content fingerprint and supplied host capabilities. It neither imports
Piper/Torch/ONNX nor probes, installs or starts a process. A syntactically valid
external manifest is not in the approved allowlist. `ProfileHealth` records
version-bound observations from the fixed D008 probe; its construction
does not execute that probe. Voice files remain external requirements referencing
the existing curated Piper catalog. See [D045 profile contract and evidence](../desktop/D045_RUNTIME_PROFILES.md).

## Private Piper runtime provisioning (D008)

`runtime.provisioning.PiperProvisioner` installs the approved profile from an
explicit offline artifact source into configured runtime storage. Every artifact
is size/hash checked before extraction. Embedded Python, pinned wheels, app-owned
worker sources and app-local MSVC DLLs form one private environment. No pip,
system installer or voice download is invoked. Fixed paths and `._pth` isolate
the worker from system Python, user packages and registry paths.

Installation is a blocking service to schedule off the UI thread. An OS lock
serializes installations; only a complete environment passing the real private
worker probe receives an atomic active pointer. Failed/interrupted candidates
remain inactive. Restart checks relative receipts, immutable file hashes and
worker revision; valid installations are reused, while corruption fails closed.
`InstalledRuntime.worker_launch()` composes with D007 and the D006 queue.
The clean-Windows gate is still pending; see [D008 behavior and evidence](../desktop/D008_MANAGED_PIPER_RUNTIME.md).

## Curated voice installation (D009)

`runtime.voice_download.PiperVoiceDownloader` uses the existing Piper catalog
and a verified D008 runtime to download one explicitly selected voice/language.
The blocking service supports progress/cancel callbacks, HTTP range resumption,
space checks and verification of the ONNX, companion config and model card.
`runtime.model_index.ModelIndex` stores immutable versions and atomically
publishes per-voice active pointers in configured model storage. Restart verifies
catalog provenance and file hashes before returning relative, resolved model and
config paths. Failed downloads preserve previous installed voices and keep their
own version inactive. It does not change a project's active voice or language.
See [D009 behavior, smoke and limitations](../desktop/D009_PIPER_VOICE_DOWNLOAD.md).

## Single-section raw audio (D010)

`application.section_audio.SectionAudioService` enqueues one immutable section
through D040, using injected voice and output ports. `ManagedSectionAudio` verifies
the approved runtime and installed D009 voice, reuses catalog selection/effective
identity and composes the D007 worker. Per-job workspaces retain checksum-validated
chunks across retries without touching immutable published media. The private
worker uses the existing TTS factory/provider and resumable chunk synthesizer;
it never opens the project or queue database.

After worker cleanup, the coordinator revalidates the request, final PCM WAV,
chunks and measured duration, then publishes raw audio and `section_audio`
metadata through the common gate. Stale results remain historical. Runtime source
delivery uses one explicit source map shared by provisioning and the build recipe;
optional native imports stay in the private worker. See [D010 contracts, managed
smoke and limitations](../desktop/D010_SECTION_AUDIO.md).

## Artifact persistence

### Section tempo derivatives (D011)

`SectionTempoService` consumes selected D010 raw audio through an injected artifact
port. `SectionTempoArtifacts` reuses the existing FFmpeg post-processor in configured
temporary storage and publishes measured output via D040. The immutable derivative
key binds raw checksum, normalized tempo and processor contract version; D005 also
records the exact raw artifact dependency. No TTS provider is called.

Raw and processed output selections remain separate. Callers explicitly request
`original` or `processed`; missing/stale processed media does not silently fall back.
Both variants and their measured `SectionAudio` parameters survive reopen. Failure
preserves previous selections, while stale completions remain historical. See
[D011 behavior, real FFmpeg evidence and limits](../desktop/D011_TEMPO_DERIVATIVE.md).

### Artifact stores

`ArtifactStore` retains its small-payload save/read/list interface. The optional
`StreamingArtifactStore` adds file/stream imports and a caller-owned read handle.
`LocalArtifactStore` now stages writes, hashes transferred bytes incrementally in
1 MiB chunks, flushes the complete file and publishes to a unique destination
without replacement. Friendly names remain separate from artifact IDs. Generated
keys and resolved destinations stay under the configured root; full hostile-path
and junction-race hardening remains D044.

Standalone roots retain atomic JSON manifest sidecars. For editable projects,
`LocalArtifactStore.for_project(repository)` uses an independently versioned index
at `artifacts/.artifacts/index.sqlite`, tied to the open D003 session/project ID.
This leaves D003's `project.sqlite` v1 unchanged and performs no migration. Both
catalogs reuse `ArtifactManifest`; only registered keys are readable as artifacts.

The catalog commits after complete file publication; SQLite and the filesystem
are not one transaction. Recovery removes incomplete staging, finishes cleanup
of committed publications, and reports unindexed complete files without adopting
or deleting them. Revision-aware job selection is provided by D040 below.
The old save/read convenience methods still use whole payloads; large-media clients
must choose the new streaming interface. Existing providers/modules remain unchanged.
See [D004 protocol, failure handling and evidence](../desktop/D004_ARTIFACT_PUBLICATION.md).

D005 stores optional versioned `desktop_dependencies` declarations in the existing
immutable `ArtifactManifest.metadata`, published with the D004 catalog record.
`ArtifactDependencyIndex` maps those records into pure dependency values and checks
consumed artifact identities/checksums and known logical bindings. Legacy records
without declarations remain untracked. This adds no tables or schema migration;
current desired requests and selections are explicit caller inputs, not a new
persistent selection store. See [D005 semantics and evidence](../desktop/D005_DEPENDENCIES.md).

### Revision-aware result publication (D040)

`ResultPublicationService` captures exact editorial revisions and a generation
token in D006's immutable enqueue snapshot. `ResultArtifactIndex` extends the
existing D004 index: queue insertion and generation reservation commit together;
registration, conditional selection, decision history and job success share one
SQLite transaction with the attached queue. Both databases require DELETE journals
and FULL synchronization. The D003 exclusive coordinator session protects revision
comparison. File publication still precedes the database transaction and uses D004
recovery; unindexed complete files remain retained orphans.

Only results matching current consumed revisions/artifacts and the latest reserved
generation become selected. Obsolete results keep their exact input/dependency
metadata and historical bytes. Completion replay returns its original decision
without rereading or reselecting. Retained selection pointers can become stale on
later edits; D005 continues to derive freshness independently.

D007 accepts a trusted coordinator completion hook after verified worker exit and
cleanup. Ordinary D006 success cannot bypass the gate for D040 jobs. Legacy job
completion and artifact imports retain existing behavior. See [D040 contracts,
transaction boundaries and evidence](../desktop/D040_RESULT_PUBLICATION.md).

D012 publishes `speech_boundary_map` within existing `section_audio` metadata
after independently checking source spans and all chunk/final PCM bytes. The map
and WAV share the D040 immutable publication and history; no new table or mutable
timing sidecar is added. Desktop synthesis v2 isolates sentence blocks, while v1
jobs retain legacy behavior. D011 derivatives carry an explicitly approximate
tempo map to the selected processed audio. See [D012 source/sample coverage and
compatibility](../desktop/D012_SPEECH_BOUNDARIES.md).

D013's `ProjectScenePlans` stores immutable JSON proposals, explicit acceptances
and audio-bound timing sets in the same project artifact store/index. Histories
are discoverable by section ID after reopen. No latest-plan fallback or automatic
acceptance is added; retiming consumes an explicit retained acceptance ID.
Checksum/current-revision checks prevent corrupt or stale data from being used
for new scene results. The legacy workflow scene module is unchanged. See
[D013 contracts and validation](../desktop/D013_SCENE_PLANNING.md).

D014's desktop script service validates the complete structured provider response
before passing new editorial revisions to D003's existing atomic save/selection.
An expected-active-revision comparison protects edits made during generation.
Manual append/edit preserves unrelated sections; invalid output and transaction
failure leave the previous script intact. No schema or artifact-store changes
are needed. The legacy workflow script module retains its original behavior; see
[D014 desktop service and compatibility](../desktop/D014_STRUCTURED_SCRIPT.md).

D015 stores brief/style revisions, visual prompts and per-scene selection events
as immutable project artifacts. Prompt metadata records D005 consumed-source
fingerprints; context bytes are reconstructed from pinned revisions before save.
The active prompt is the head of a validated selection-event chain, with an
expected-previous-ID check under the exclusive coordinator session. No database
schema or mutable selection file is added. D004 recovery applies, including
truthful handling of cleanup failure after index commit. See [D015 persistence,
manual ownership and selective freshness](../desktop/D015_VISUAL_PROMPTS.md).

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

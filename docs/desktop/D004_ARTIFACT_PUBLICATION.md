# D004 — Streaming immutable artifact publication

Result: **Completed — PASS**, 2026-09-12. Branch:
`feat/d004-streaming-artifact-publication`, based on master `6818e9a`.

## Existing storage analysis

Before D004, `ArtifactStore` offered `save_artifact(bytes | str)`,
`read_artifact() -> bytes` and sorted, prefix-filtered manifest listing.
`LocalArtifactStore` converted text to UTF-8, hashed the whole payload and called
`Path.write_bytes()` directly at the final path. Only afterward did it write
`.artifacts/<artifact_id>.json` using `write_text()`. Neither write used staged
publication; there was no project artifact index or publication recovery.

`ArtifactManifest` already represented ID, friendly name, type/version, workflow,
module, relative key, SHA-256, size, metadata and creation time. Its generated key
combined workflow/module segments with an artifact ID and filename. Consequently
repeated friendly names already received different keys in the local store;
D004 adds enforced no-replacement finalization rather than claiming IDs were absent.

Twelve existing modules use the small-payload interface, including script/scene
generation, captions, research, export, publishing, voiceover and video rendering.
They consume the existing manifest format and metadata aliases. Voiceover currently
passes `audio_bytes`; video rendering currently stores a provider reference string,
not a playable MP4. TTS preview/chunk assembly and some providers also contain
whole-file reads. D004 does not rewrite those integrations or claim to implement
rendering/TTS. `MockStorageProvider` has deterministic in-memory identities and
remains unchanged. Storage/manifests, module and workflow tests preserve these
contracts.

## Architecture chosen

- `ArtifactStore` retains its original three methods. An optional
  `StreamingArtifactStore` protocol adds `import_file`, `import_stream` and
  `open_artifact`. Older providers need not implement that capability.
- `LocalArtifactStore(root)` retains atomic JSON sidecars as the authoritative
  catalog for legacy standalone roots. Existing sidecars stay readable.
- `LocalArtifactStore.for_project(repository)` uses the open D003 coordinator
  session and `workspace/artifacts/.artifacts/index.sqlite`. Its independent
  format is version 1, application ID `0x41494341` (AICA), bound to the exact
  project ID and relative root reference `artifacts`. Rows have unique artifact
  identities/keys, checksum, size and the existing serialized `ArtifactManifest`.
- D003's `project.sqlite` schema and contents are unchanged. Index creation is an
  explicit optional storage operation, not a project migration. Unknown index
  versions/owners are rejected. A legacy sidecar root is not silently converted
  to a new empty project index. Future consolidation/migrations belong to D042.
- Every index access requires a live repository on its coordinator thread.
  Operations use short SQLite transactions/connections, closed before workspace
  relocation. There is no transaction joining this index to the filesystem or
  project state, and no active section/audio pointer is changed by D004.

Composition example (within an already open `ProjectSession`):

```python
from app.storage.local_store import LocalArtifactStore

store = LocalArtifactStore.for_project(session.repository)
issues = store.recovery_report
artifact = store.import_file("voice.wav", private_output_path,
                             {"artifact_type": "voiceover"})
with store.open_artifact(artifact.storage_key) as stream:
    block = stream.read(1024 * 1024)
```

`for_project` performs journal recovery. Standalone legacy roots call `recover()`
explicitly when no writer is active. Serialize imports and recovery per root;
this is a local coordinator API, not a concurrent multi-writer storage service.

## Artifact publication flow

1. Generate a unique artifact identity and relative key. Snapshot JSON metadata
   before reading media. Unsafe separator/drive syntax cannot escape via generated
   suffixes or keys; resolved destinations must stay under the root.
2. Create a private `.artifacts/staging/publication-*/` directory and durable
   `intent.json`. Stream bytes into its `payload` file with bounded reads.
3. Update SHA-256 only after each complete write. Flush/fsync the payload, check
   the written size, then atomically replace the private intent with complete
   checksum/size metadata. An empty artifact is allowed by the existing contract.
4. Publish complete payload bytes to the unique final key without replacing an
   existing destination. Windows uses `os.rename`; POSIX uses atomic hard-link
   creation followed by unlink of the staging name, since POSIX rename overwrites.
   Both require the same filesystem and fail safely when unsupported.
5. Commit the manifest: atomic no-replacement sidecar publication for a legacy
   root, or an INSERT transaction into the project index. INSERT cannot replace
   another identity/key. Registration happens only after complete final bytes exist.
6. Remove the completed private journal. If the process stops first, recovery can
   recognize the committed artifact and finish journal cleanup.

Only cataloged keys are readable through the public API. An unindexed final file
is not advertised by listing and cannot be read as a published artifact.
The atomic-operation choice follows
[Python's rename semantics](https://docs.python.org/3/library/os.html#os.rename).
`os.replace` is confined to private intent metadata; it never replaces a published
media file or manifest.

## Streaming behavior

Imports request at most **1 MiB per read**, use one incremental SHA-256 and do not
call `Path.read_bytes()`. A caller-owned binary stream is consumed from its current
position and stays open. File import opens/closes its own source handle. Invalid
stream values, excessive returned chunks and incomplete writes fail publication.
Metadata reflects transferred bytes, not caller-provided hash/size guesses.

The old `save_artifact(bytes | str)` delegates to the same protocol through
`BytesIO`; callers have already materialized that payload. `read_artifact()` still
returns the whole payload for compatibility. Large-media clients must use file/
stream import and `open_artifact`, not these convenience methods. Recovery may
rehash a committed file with a leftover journal, also using bounded reads.

## Crash / failure behavior

| Failure boundary | Catalog state and recovery |
| --- | --- |
| Interrupted transfer or failure before file publication | No new record; discard incomplete staged payload/journal |
| Complete file published but registration never commits | No new record; report `orphan`, retain final bytes and journal for inspection |
| Index insert followed by exception/process exit before commit | SQLite rolls back; report the unindexed final as `orphan` |
| Commit succeeds, process stops before journal cleanup | Keep the published record/file; verify bounded checksum/size and clean journal (`committed`) |
| Torn/unsafe journal | Report `invalid`, preserve it in place, never auto-adopt it |
| Colliding or inconsistent retained record/file | Report `inconsistent`; preserve existing artifacts |

Recovery scans only this publisher's journal namespace, not all workspace files.
It is deterministic: discarded/committed journals disappear after cleanup; orphan
reports repeat until explicitly resolved. Unindexed finals are deliberately not
auto-adopted or deleted: a collision may belong to another publication, and no
revision/job policy is available in D004. This is not garbage collection.

An exception after commit can mean publication succeeded but cleanup did not.
Do not delete the final file as compensation; recovery consults the committed
catalog. Retrying creates a new identity rather than overwriting existing bytes.

## Tests and acceptance evidence

25 new tests cover bounded multi-chunk stream/file input (with a failing
`Path.read_bytes` guard), exact hash/size, caller stream ownership, duplicate names,
forced ID collision, unsafe generated suffixes, old sidecar compatibility,
transfer/move/registration failure, torn intents, index ownership/version refusal,
closed sessions, unchanged D003 database bytes and relocated project reopen.

Five subprocess cases terminate via `os._exit` during transfer, before move, after
move, after index INSERT and after index commit. Assertions verify visibility,
file checksums, rollback, preserved records and repeatable recovery after restart.

| Acceptance criterion | Result |
| --- | --- |
| Repeated friendly names retain distinct immutable artifacts | PASS |
| Recorded checksum and size match published bytes | PASS |
| Injected failure cannot advertise partial media | PASS |
| Large-file import uses bounded reads without whole-file loading | PASS |
| Same-filesystem atomic publication without replacement | PASS on tested Windows filesystem |
| Project-bound artifact index and D003 compatibility | PASS |
| Deterministic restart handling of incomplete/unindexed publication | PASS |
| Existing ArtifactStore clients and deterministic mock behavior | PASS |

Focused validation (66 passed):

```text
python -m pytest backend/tests/unit/test_streaming_artifacts.py backend/tests/integration/test_artifact_publication.py backend/tests/unit/test_artifact_store.py backend/tests/unit/test_t009.py backend/tests/unit/test_t010.py backend/tests/unit/test_export_manifest.py backend/tests/unit/test_project_repository.py backend/tests/integration/test_durable_project.py backend/tests/integration/test_long_form_workflow.py backend/tests/integration/test_export_bundle.py
```

Full validation: `python -m pytest backend/tests` — **612 passed**, 33.06 s.
`git diff --check` and new-file whitespace validation — PASS.
Environment: Windows, isolated Python 3.11, temporary synthetic files, no Qt,
provider network calls, downloaded models or real private media.

## Self-review and remaining risks

The diff keeps provider/module code and D003 unchanged. Media hashing follows
successful writes, metadata registration is last, neither final files nor index
rows are overwritten, and ambiguous commits preserve data. Existing metadata
aliases, key structure for normal names and legacy sidecars remain compatible.
The additional restriction is intentional: unregistered files are not readable
as published artifacts.

No D005 dependency/freshness graph, D006 jobs, D040 revision-aware publication,
D047 collector, TTS, renderer, UI, API delivery or cloud storage was implemented.

Risks/limits: sudden power loss and unusual/network filesystems need separate
durability qualification; POSIX fallback is not a Linux acceptance claim. External
programs can still modify files outside this API. Comprehensive path/junction race
hardening is D044. No codec-specific validity checks are claimed here; producers
must validate media semantics. Orphans/invalid journals remain for inspection.
Index initialization interrupted before its first commit may leave an unversioned
index that is rejected, not automatically reinitialized. Backups must preserve the
closed workspace including both databases and artifact files.

Recommended next action: review this branch and its publication/index boundary,
then merge when accepted. D005 requires a separate explicit task invocation.

# API and storage

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

## Artifact persistence

`ArtifactStore` is the abstract save/read/list interface. `LocalArtifactStore(root)`
creates stored bytes and manifest sidecars under an explicitly supplied root.
Storage keys are relative, normalized and checked against traversal. Metadata
includes the owning workflow run, producing module, artifact type, version,
checksum and storage reference through `ArtifactManifest`.

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

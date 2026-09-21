# Provider contracts

These are current contracts. The [desktop plan](../desktop/IMPLEMENTATION_PLAN.md)
adds managed worker composition, an image-generation boundary and real media
results without putting provider selection in the UI or workflow engine.

The protocols in `backend/app/providers/interfaces.py` are the executable contract.
Every provider has `provider_type` and `provider_name`. `ProviderRegistry` registers
and resolves implementations by type/name. `validate_provider_availability` checks
requirements for enabled modules before direct engine execution.

| Protocol | Operations | Current implementations |
| --- | --- | --- |
| `LLMProvider` | `generate_text(prompt, context) -> str`; `generate_structured(prompt, schema) -> dict` | Deterministic mock |
| `TTSProvider` | `capabilities()`; `effective_synthesis_identity(voice_config)`; `synthesize(text, voice_config) -> TTSSynthesisResult` | Mock, Chatterbox V3, Piper, evaluation-only XTTS-v2 |
| `TranscriptionProvider` | `transcribe(audio_ref) -> dict` | Mock |
| `CaptionProvider` | `generate_captions(audio_ref, transcript_ref) -> dict` | Mock |
| `AssetProvider` | `find_assets(query)`; `prepare_asset(asset_ref)` | Mock |
| `VideoRendererProvider` | `render(scene_plan, audio_ref, captions_ref) -> dict` | Mock reference output |
| `StorageProvider` | `save_artifact(name, content, metadata)`; `read_artifact(key)`; `list_artifacts(prefix)` | Mock storage adapter; real local persistence is also available through `ArtifactStore` |
| `PublishingProvider` | `publish(export_bundle, target)` with `PublishingRequest` / `PublicationResult` | Mock and optional YouTube adapter |

`build_tts_provider` and `build_publishing_provider` compose adapters from
`ProviderConfig`. Modules receive implementations through constructors; provider
registration alone is not application wiring. Add adapters at this boundary,
keeping provider-specific settings/errors out of the core execution engine.

## Desktop MP4 rendering (D019)

`VideoRenderService` uses an injected `RendererPort`. `FFmpegRenderer` returns
measured `RenderedVideo` evidence after actual MP4 encoding, probing and full
audio/video decoding; D040 publishes the bytes. The legacy
`VideoRendererProvider` dictionary contract above remains unchanged. See
[D019 composition and validation](../desktop/D019_STATIC_IMAGE_MP4.md).

## Timeline preview (D046)

`PreviewService` is UI- and provider-independent. It uses the exact active D018
snapshot, a renderer identity-bound cache key and a media/cache port. The FFmpeg
adapter's proxy profile shares D019's source trimming, fit/fill, cancellation,
probe and full-decode path at 640×360; final export remains 1280×720. The Qt
adapter never plays a result after its timeline or selected media becomes stale.
See [D046 preview behavior and evidence](../desktop/D046_SCENE_FILM_PREVIEW.md).

## TTS

`TTSSynthesisResult` carries actual audio bytes, sample rate, duration, format,
provider name and metadata. Shared assembly validation checks readable uncompressed
PCM, complete frames and compatible channel count, sample width and sample rate
across chunks. Adapter/preview contracts can impose narrower audio requirements;
adapters retain truthful sample rates. A path or URI is not a WAV payload.

Capabilities are static, lazy metadata. Effective synthesis identity is
request-specific and includes resolved settings and reference/asset checksums.
Cache reuse depends on this identity and text, not just a provider label.
Chatterbox `chatterbox_v3` exposes model `v3`, builtin/reference voices; Piper uses
the curated model keys in `piper_catalog.py`; `xtts_v2_eval` requires approved
reference audio and remains evaluation-only. Production configuration rejects it.

The catalog adapts explicit registrations without constructing real runtimes.
`tts/selection.py` maps a catalog selection into `providerConfig.tts` and
`voiceConfig`. Tempo lives under `voiceConfig.postProcessing.tempo`; it is not a
native speaking-rate capability. Preview and production caches are separate.
Optional packages/model loading happen lazily; setup and real synthesis remain
explicit actions. Preserve provenance and license evidence already recorded in
[TTS documentation](../INDEX.md#tts).

## Publishing

`PublishingModule.publish` is a separate application operation gated on an approved
handoff. YouTube transport configuration and credentials stay behind the adapter;
construction performs no network call. Offline tests inject a fake transport.
Publication identity supports idempotent behavior within the implemented boundary;
durable retry coordination across application restarts remains roadmap work.
Localization records manual platform facts and fallback metadata. The code does
not expose an automatic-dubbing endpoint. See [publishing](../publishing/YOUTUBE_HANDOFF.md).

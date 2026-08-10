# TTS Selection API

This contract describes the provider-neutral backend sequence for a future voice picker. A client discovers compatible catalog entries, requests a short preview, persists the same selection in the existing `WorkflowConfig`, and later composes the selected provider for production voiceover. Provider, model, and voice remain separate identifiers throughout the sequence.

## Catalog discovery

Call `GET /api/v1/tts/catalog`. The optional `language` filter accepts a normalized language tag. The optional `usagePolicy` filter accepts `production` or `evaluation_only`; `evaluation_only` includes production-capable entries as well as evaluation-only entries. Responses are deterministic and ordered.

Each provider includes `id`, `displayName`, `usagePolicy`, `supportedLanguages`, `capabilities`, and `models`. Capabilities include supported languages and voice modes, whether approved reference audio is required, whether native speaking rate is supported, and the usage policy. Each model includes its stable `id`, label, provider id, supported languages, runtime/asset requirements, and `voices`. Each voice includes its stable `id`, provider/model ids, `voiceMode`, `supportedLanguages`, `previewSupported`, `referenceAudioRequired`, and optional JSON-safe `publicMetadata`.

Catalog discovery is descriptive only. It does not load an optional runtime or model, initialize a GPU, download assets, make a network request, or synthesize audio. Production discovery contains Chatterbox Multilingual V3 and curated Piper voices; XTTS-v2 is marked evaluation-only. Experimental providers, including MOSS-TTS, are not registered.

## Create and play a preview

Call `POST /api/v1/tts/previews` with camelCase JSON:

```json
{
  "provider": "chatterbox_v3",
  "model": "v3",
  "voice": "builtin",
  "language": "en",
  "tempo": 1.1,
  "text": "A short sample of the selected voice.",
  "synthesisSettings": {
    "cfgWeight": 0.4
  }
}
```

`text` must be non-empty after whitespace normalization and at most 400 characters. The provider, model, voice, language, policy, and synthesis settings must be mutually compatible with the catalog. The response contains `previewId`, `audioUrl`, the effective selection fields, `tempo`, `durationSeconds`, `checksum`, and `cached`. It never contains an absolute storage path.

Retrieve playback bytes from the returned opaque URL, shaped as `GET /api/v1/tts/previews/{previewId}/audio`. A successful response has content type `audio/wav` and contains validated mono 16-bit PCM WAV. Treat both `previewId` and `audioUrl` as opaque: clients must not construct filesystem paths or derive storage locations from them.

For a reference voice, send only `referenceAudioArtifactId`, an opaque identifier for audio already stored and approved through the controlled artifact workflow. Do not send a path, `file:` URL, audio content, checksum, or approval record in the preview request. The backend resolves and verifies the artifact, checksum, and approval metadata before provider composition.

## Stable errors

- `400` means a catalog filter, preview selection, text, setting, usage policy, tempo, or approved-reference requirement is invalid.
- `404` means an opaque preview id is unknown, malformed, expired, or otherwise unavailable. The response does not reveal whether any local path exists.
- `422` means the JSON shape or field types do not satisfy the strict API schema, including extra path-like fields.
- `500` uses the stable message `TTS preview request failed.` Unexpected runtime details, paths, credentials, and tokens are redacted.

Clients should display the returned `detail` without trying to infer provider runtime internals. A corrected request may be retried; identical valid preview requests are safe to repeat.

## Persist the canonical workflow selection

There is no second persisted TTS-selection object. Persist the choice with the existing workflow-config endpoint, `POST /api/v1/projects/{projectId}/workflow-configs`, using `providerConfig`, `voiceConfig`, and the top-level workflow `language`:

```json
{
  "projectId": "project_opaque_id",
  "workflowPreset": "short_video",
  "contentType": "short_video",
  "contentGenre": "news",
  "durationProfile": "60s",
  "targetPlatform": "youtube_shorts",
  "language": "en",
  "tone": "neutral",
  "providerConfig": {
    "tts": {
      "providerName": "chatterbox_v3",
      "enabled": true,
      "settings": {
        "provider": "chatterbox_v3",
        "usagePolicy": "production",
        "modelVariant": "v3",
        "cfgWeight": 0.4
      }
    }
  },
  "voiceConfig": {
    "voiceId": "builtin",
    "voiceMode": "builtin",
    "postProcessing": {
      "tempo": 1.1
    }
  }
}
```

The canonical mapping is:

- catalog provider -> `providerConfig.tts.providerName` and `providerConfig.tts.settings.provider`;
- catalog model -> the provider's declared model setting (`modelVariant` for Chatterbox/XTTS or `modelKey` for Piper);
- catalog voice and mode -> `voiceConfig.voiceId` and `voiceConfig.voiceMode`;
- usage policy and relevant provider synthesis settings -> `providerConfig.tts.settings`;
- approved reference identity -> opaque `voiceConfig.referenceAudioArtifactId` plus approved metadata, never a path;
- selected language -> top-level `WorkflowConfig.language`, which remains authoritative;
- tempo -> `voiceConfig.postProcessing.tempo` only.

Tempo is provider-neutral post-processing applied after PCM synthesis in both preview and production. It is not a native speaking-rate setting and does not alter the provider/model/voice identity. Do not add `speakingRate`, `speed`, `lengthScale`, or another native rate control merely to represent tempo. Provider-specific settings such as Piper `lengthScale` are distinct synthesis inputs and may be used only when the catalog mapping declares them.

Production validation rejects stale combinations, unsupported languages, missing approved references, and evaluation-only XTTS under production policy before optional runtime or model loading.

## Cache and artifact boundaries

Preview identity includes provider, model, voice, language, normalized preview text, relevant effective synthesis settings, approved-reference checksum when applicable, and final tempo. Identical requests return the same `previewId`; once generated, `cached` is true and synthesis is not duplicated.

Preview audio/manifests and production narration chunks use independent controlled roots, cache keys, manifests, and output artifacts. A preview URL is never a production artifact reference, and a production chunk is never served by the preview endpoint. Preview-only text, cache keys, URLs, and output metadata are excluded when comparing the effective provider/model/voice/language/synthesis identity used by preview and production.

## Future UI sequence

1. Request the catalog with the workflow language and intended usage policy.
2. Present compatible provider, model, and voice descriptors without importing provider-specific client code.
3. Submit a short preview request and play only the returned opaque `audioUrl`.
4. Repeat the request freely; an identical request should return the same cached preview.
5. Translate the selected descriptors once into canonical `providerConfig`, `voiceConfig`, and `WorkflowConfig.language` fields.
6. Persist those fields through the existing workflow-config API.
7. Start production only after workflow validation succeeds; the workflow engine and `VoiceoverModule` remain provider-neutral and receive an already composed provider.

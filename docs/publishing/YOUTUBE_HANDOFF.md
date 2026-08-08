# YouTube-ready export and publishing handoff

The generic `ExportModule` can be composed with `YouTubeHandoffBuilder`. The
builder consumes the existing generic export manifest and produces a
deterministic `platform_handoff.json` with source language, upload metadata,
relative checksummed artifact references, localization intent, final approval,
and an idempotency identity. It requires a real video artifact. Voiceover,
English SRT/JSON captions, and thumbnail entries are explicit even when an
optional artifact is unavailable.

Persisted handoffs never contain absolute local paths, OAuth credentials,
access tokens, API keys, or client secrets. `WorkflowConfig.language` remains
the source language; export localization targets are separate.

## Supported API writes

`YouTubePublishingProvider` uses the documented YouTube Data API operations:

- `videos.insert(part="snippet,status")` uploads the video and sets title,
  description, tags, category, metadata language, privacy, made-for-kids, and
  synthetic-media disclosure.
- `captions.insert(part="snippet")` uploads the existing timed English SRT.

The safe default privacy status is `unlisted`. Google client imports are lazy
and live in the optional `youtube` dependency group. Provider construction
does not load credentials, start OAuth, open a browser, or make a request.
The runtime consumes an already-authorized credential-file reference from
`YOUTUBE_CREDENTIALS_FILE`; secrets are never serialized by this application.
The credential must already include the documented
`https://www.googleapis.com/auth/youtube.force-ssl` scope, which covers both
video upload and caption insertion. This repository intentionally does not run
the interactive OAuth consent flow.

`snippet.defaultLanguage` identifies the language of the title and
description. The documented insert/update write lists do not include
`snippet.defaultAudioLanguage`, so the provider does not send it. Confirm the
original video language manually in YouTube Studio.

Automatic dubbing is also managed in YouTube Studio, not through a supported
YouTube Data API write. For an eligible channel, enable automatic dubbing,
enable manual review before publication, confirm English as the original
language, wait for the Polish dub, then record the manual result in the
localization handoff. The application never claims it generated or published
the platform dub.

If the Polish dub is rejected or unavailable, the handoff can require a custom
audio fallback and later record its relative artifact reference, SHA-256
checksum, approved label, and provenance. Custom audio upload remains a manual
future integration.

## Official references

- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/videos/update
- https://developers.google.com/youtube/v3/docs/captions/insert
- https://support.google.com/youtube/answer/15569972
- https://support.google.com/youtube/answer/13338784

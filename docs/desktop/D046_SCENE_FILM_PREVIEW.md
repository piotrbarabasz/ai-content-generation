# D046 — Scene and whole-film preview

## Implementation boundary

`PreviewService` consumes the active D023 `TimelineEdit`; it does not compile or
reinterpret a second timeline. Before every scene/proxy operation and again before
presenting a result, it verifies both the active timeline event and D019's current
retained image/audio selections. A late result is returned with `current=False`,
and the Qt panel refuses to load it into the player.

Scene preview is deliberately not a scene MP4. `ProjectPreviewMedia` stages the
same verified D019 inputs, applies the same EXIF/alpha image normalization and
writes a PCM WAV containing exactly the clip's half-open source sample range.
PNG/WAV cache entries are checksum-validated. D023 measured sentence trims are
accepted; arbitrary or stale ranges still fail through the shared boundary rules.

Film proxy uses `FFmpegRenderer` with the fixed `proxy-mp4-360p25-v1` profile:
640×360, 25 FPS, H.264/yuv420p and mono AAC/48 kHz. Fit/fill policy, clip order,
frame rounding and source-sample trims come directly from the D018 snapshot. The
existing final profile remains 1280×720 and unchanged. Both profiles use the same
encode/probe/full-decode implementation and owned-process cancellation.

The proxy cache key binds the complete canonical timeline payload and renderer
identity, including hashes of FFmpeg and ffprobe. Cache reads verify timeline ID,
file size and SHA-256. Corrupt entries are regenerated. Cache/work files live
under configured project storage and are regenerable; retained artifact history
is not changed. Changing an active image makes the pinned timeline stale, so its
old proxy is never treated as current. Refreshing the D023 snapshot creates a new
timeline ID/key. No TTS, image generation, job publication or provider call is
made by preview.

The desktop adds a Scene / film preview dock. It shows a normalized still while
playing the exact scene WAV, or plays the generated proxy in `QVideoWidget`.
Timeline edits stop playback and rebind the controls. Image import/generation/
selection signals recheck freshness immediately. Rendering is advanced from the
Qt event loop, exposes progress/cancel, and project switching/close is guarded
until native-process cleanup completes. Missing FFmpeg disables preview without
constructing an optional provider or blocking the rest of the editor.

## Acceptance evidence

- Snapshot/cache tests pin the full timeline plus renderer identity, reuse a
  valid cache entry, regenerate a corrupt entry and forward cancellation.
- A real SQLite/artifact fixture trims a measured sentence range and proves the
  same start sample reaches both the proxy filter and successful D040 final
  publication. Scene WAV frame count equals the selected half-open range.
- Selecting a replacement image invalidates the existing proxy before cache use;
  recorded TTS calls remain unchanged.
- Concurrent/late completion tests mark obsolete output non-current, and Qt tests
  prove that non-current output is not assigned to `QMediaPlayer`.
- `packaging/d046/smoke.py` rendered a 640×360 proxy from an exact snapshot with
  selected samples `[12000, 84000)`, then used the existing standalone D002 bundle
  with Python/Qt variables removed and System32-only `PATH`. Packaged Qt 6.11.2
  decoded 38 video frames and 72,704 AAC audio frames, advanced to 1,520 ms and
  reached end-of-media. Evidence is retained locally under ignored
  `.tmp/d046-packaged-smoke-v2/`.

The packaged smoke is automated and muted on the Windows developer host. It does
not claim D002's still-pending clean-Windows or human picture/sound observation;
that release gate remains explicit.

## Validation

Environment: Windows developer host, isolated CPython 3.11.9, PySide6/Qt 6.11.2,
real FFmpeg/ffprobe 8.1.2-full_build. All fixtures are synthetic and offline.

Focused command:

```text
python -m pytest backend/tests/unit/test_preview.py backend/tests/integration/test_preview.py backend/tests/integration/test_preview_panel.py backend/tests/integration/test_scene_panel.py
```

Result: 15 focused tests passed. The full backend suite passed 1502 tests with
11 existing optional skips (1513 collected). `compileall`, `pip check` and
`git diff --check` also passed. Counts and the deferred D002 gate are recorded in
the D046 implementation-plan entry.

# D032 — Synchronized optional captions

## Implemented boundary

D032 adds a desktop caption path beside the legacy provider-driven workflow
`CaptionsModule`. It consumes an explicit D018 timeline plus one explicit D031
alignment for every selected section. Construction performs no transcription,
provider call, model import, network request or audio analysis.

Only a D031 alignment whose outcome is `complete` is eligible. Project, section,
revision, source-text checksum/ranges, audio artifact/checksum, sample rate and
frame count must match each selected timeline clip exactly. Every selected word
must be wholly contained in a half-open clip sample range. Omitted,
low-confidence, provider-insertion, mismatched or boundary-cut input fails rather
than estimating timing or silently dropping text.

Aligned source words are mapped to exact rational output offsets and grouped in
timeline order at sentence, clip, word-count, character-count and duration
boundaries. Segment text is sliced from the authoritative section text, retaining
inter-word punctuation. Millisecond conversion uses one deterministic half-up
rule. Intervals are positive, monotonic, non-overlapping and bounded by the exact
selected audio duration. No lead-in/out padding, karaoke timing or animated style
is added.

## Artifacts and rendering

`SynchronizedCaptionTrack` is immutable and content-addressed. It binds project,
timeline, language, duration, ordered alignment IDs and caption segments. One
explicit export publishes canonical JSON plus independent UTF-8 SRT and static ASS
artifacts. The project store verifies their checksums, bytes, shared dependency
declaration and serializer output when reopening a published track. Existing
tracks and rejected/partial artifacts are never overwritten.

`VideoRenderService.enqueue(..., captions=published)` is the opt-in burn-in
toggle. The render request pins the caption-track and ASS artifact identities and
checksums. Private render staging revalidates the complete published set and copies
only the selected ASS bytes into the unique work directory. `FFmpegRenderer` then
adds one static libass subtitle filter before the unchanged H.264/AAC encode,
probe and full-decode gates. With `captions=None` (the default), the request has no
caption edges, stages no caption bytes and needs no caption/alignment provider.

The legacy `CaptionTrack`, `CaptionsModule` and `CaptionProvider` contracts remain
compatible. D032 does not make D031's pending real-WhisperX gate pass; it accepts
only already complete alignment evidence. The user's explicit D032 continuation
authorizes this downstream work while that separate D031 runtime gate remains
open.

## Validation evidence

Validation on 2026-09-23, Windows / isolated Python 3.11.9:

- focused D032, legacy captions and D019 renderer suite: 47 passed in 145.06 s;
- real FFmpeg 8.1.2/libass smoke uses a generated black image, generated 220 Hz
  tone and synthetic measured words only; it proves the caption appears only in
  its measured interval and the output still passes profile, frame, duration,
  probe and full audio/video decode gates;
- full `python -m pytest backend/tests -o addopts='' -q --tb=short`:
  1678 passed, 11 existing optional tests skipped in 642.13 s;
- `compileall`, `pip check` and `git diff --check`: PASS; no AI, network, model
  download or private media is used.

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Caption intervals fit selected audio and text order is preserved | Domain tests cover exact source/audio identity, sample-span mapping, sentence/punctuation grouping, bounds, serialization and rejection of low-confidence, mismatched and boundary-cut inputs. Project integration exports and reopens JSON/SRT/ASS. | PASS |
| Disabled captions require no provider | The default render request contains no caption payload or dependency edge; provider-free composition only wraps project artifacts. Legacy provider composition is untouched. | PASS |
| Optional output is playable/exportable | Actual FFmpeg/libass burn-in produces an H.264/AAC MP4 whose caption pixels are absent before and present during the measured interval; independent SRT and ASS artifacts are checksum-verified and privately stageable. | PASS |

## Changed files

- `backend/app/domain/caption_track.py`
- `backend/app/application/captions.py`
- `backend/app/storage/captions.py`
- `backend/app/desktop/caption_composition.py`
- `backend/app/domain/render_result.py`
- `backend/app/application/video_render.py`
- `backend/app/storage/video_render.py`
- `backend/app/providers/ffmpeg_render.py`
- `backend/tests/unit/test_synchronized_captions.py`
- `backend/tests/integration/test_synchronized_captions.py`
- `docs/architecture/domain-model.md`
- `docs/architecture/api-and-storage.md`
- `docs/architecture/provider-contracts.md`
- `docs/desktop/IMPLEMENTATION_PLAN.md`
- `docs/desktop/D032_SYNCHRONIZED_CAPTIONS.md`

Focused reproduction:

```text
python -m pytest backend/tests/unit/test_synchronized_captions.py backend/tests/integration/test_synchronized_captions.py backend/tests/integration/test_video_render.py backend/tests/integration/test_ffmpeg_render_media.py backend/tests/unit/test_t080.py backend/tests/unit/test_render_contract.py -o addopts='' -q -x --tb=short
```

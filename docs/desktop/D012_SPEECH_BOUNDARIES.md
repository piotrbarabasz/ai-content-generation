# D012 — Measured speech boundary map

D012 follows the merged D010/D011 implementation and reuses D040 publication.
The service remains independent of providers and UI. No database schema, scene
model or approval selection is changed.

## Source and measurement contract

New desktop `section_audio.synthesize` requests use algorithm version **2**.
`sentence_chunks` reuses the existing deterministic paragraph/sentence parser,
but does not pack separate sentences into the same TTS call. Oversized sentences
still split at the technical word limit. All their chunks retain the same
sentence ID and complete sentence source range. IDs are local to the section's
text, deterministic across voice/tempo/chunk-limit changes, not global entities.

Each technical chunk retains both the existing normalized narration offsets and
an additional `SourceSpan` in the original section text. Original offsets are
half-open Python Unicode character ranges. Leading whitespace belongs to the
first chunk; inter-chunk and trailing whitespace belongs to the preceding chunk.
Consequently the ranges partition the exact original text, not just its words.
Punctuation and paragraph separators are preserved in source coverage.

`ChunkManifest.source_span` persists that identity through synthesis and retry.
The coordinator independently reconstructs expected chunks from the frozen job,
checks source spans, reads and validates every WAV, and verifies that their exact
concatenated PCM bytes match the assembled output. Only then does it construct
`SpeechBoundaryMap` and publish it inside `section_audio.speech_boundary_map`.

The immutable map binds text SHA-256/length, WAV SHA-256, sample rate and total
frame count to contiguous technical sample intervals. `blocks` groups these
measurements by whole sentence, preserving the distinction between editorial
sections, sentence blocks, technical chunks and render scenes. The constructor
rejects gaps, overlap, empty intervals, duplicate/reappearing identities, invalid
sentence ranges and incomplete text/WAV coverage. `SectionAudio.from_manifest`
validates the map against retained audio measurements and exposes it to callers.

Raw method/quality are `measured_chunk_assembly` / `measured_sentence_blocks`.
Frame boundaries include all provider silence; they are assembly boundaries,
not detected speech onsets, word timings or forced alignment. Duration in seconds
is obtained by dividing a sample position by the map's sample rate.

## Tempo and selected audio

D011 carries the validated raw map to each processed WAV and exposes it with the
existing original/processed selection. Internal positions use the **measured**
output/input frame ratio, not a guessed duration or merely the requested tempo.
Shared integer half-up rounding gives identical adjacent boundaries and an exact
last endpoint at the processed WAV's measured frame count. Incompatible rates or
rounding that collapses a technical interval are rejected before publication.

FFmpeg atempo does not report exact internal source positions. Processed maps
therefore explicitly record `measured_duration_ratio` /
`approximate_internal_positions` and the original WAV checksum. Full coverage is
exact; internal timing is approximate, including when requested tempo is 1.0.
The raw map, raw bytes, earlier variants and unrelated sections are unchanged.
Tempo performs no TTS calls and cannot recursively retime an already mapped
derivative. Existing D040 stale/cancel/publication rules continue to apply.

## Compatibility and limits

Version 1 synthesis jobs still use legacy chunk packing and publish without a
sentence map. Historical audio with no map remains readable and usable for tempo;
no sentence timing is fabricated and no old artifact is rewritten. An explicit
new version 2 synthesis is needed to obtain measured boundaries for that audio.
Legacy `chunk_narration` behavior and normalized offsets remain unchanged.

The private-worker source closure includes the new pure domain module. Its
fingerprint changes; a managed runtime must be provisioned/refreshed through the
existing verified installer before new generation. The smoke uses a fresh runtime
from approved cached artifacts. No optional concrete provider is imported by the
boundary domain, and no extra Python dependency is introduced.

Sentence isolation changes the context supplied to TTS and may affect prosody.
The existing heuristic parser is not a linguistic segmentation model. Very long
sentences can still have audible technical joins. Manual comparison is required;
automated coverage checks cannot certify naturalness. Word alignment remains
D031 work and semantic scene planning remains D013 work.

## Validation evidence — 2026-09-14

- Focused suite: **114 passed in 9.35 s**, including **36 new D012 cases**.
- Fixtures cover variable-length PCM and leading/trailing silence, exact original
  Unicode/whitespace coverage, punctuation/paragraphs, repeated and long sentences,
  shared identities under different chunk limits, incompatible PCM, corrupt source
  evidence, retry/reuse, publication/reopen, actual tempo selections, no additional
  TTS, legacy version 1 and an isolated private-worker source-bundle import.
- `python -m pytest backend/tests`: **1007 passed in 69.77 s**.
- `git diff --check` and new-file whitespace/EOF/UTF-8 checks: **PASS**.
  Relative documentation links resolve; task-scope comparison with HEAD confirms
  only D012 changed. All 16 changed/new files belong to this task.
- Actual managed Piper + FFmpeg smoke: **automated PASS**; four sentence blocks,
  five technical chunks, 282 original characters, exact full raw/processed
  coverage, retained raw/history bytes and successful selection/map reopen.

All real WAVs are mono signed 16-bit PCM at **22050 Hz**:

| Variant | Frames | Measured seconds | SHA-256 |
| --- | --- | --- | --- |
| Legacy packed | 388864 | 17.635555556 | `ca1e9769f68f106920aeaf4a10aa7a811fa47daf3fd2e174d5fc111f20fc0f39` |
| D012 original | 391936 | 17.774875283 | `13309c4715df8f416e0c19e812365dfd42f44ec022f9e182869e96db9ea27fdf` |
| Tempo 0.8 | 489861 | 22.215918367 | `eae06c780c4143f66e4e810aafcb80de937efd57a36dac04bb919d738bfbd86c` |
| Tempo 1.25 | 313599 | 14.222176871 | `4a9049a8eab7972fa53f1cd6556b04846069de51ec3b8a0152741838b2c521ad` |

Reproduce outside default offline pytest/CI:

```powershell
python packaging/d012/smoke.py --runtime-root <fresh-ignored-runtime-root> --runtime-cache <verified-D045-cache> --model-root <installed-D009-model-root> --output <new-ignored-output-directory>
```

Local report and listening fixtures: `.tmp/d012-managed-smoke/report.json`,
`LISTENING.md`, `legacy-packed.wav`, `original.wav`, `tempo-0.8.wav` and
`tempo-1.25.wav`. These files and all runtime/model materials remain ignored.

**Manual prosody comparison: pending.** Compare missing/repeated words, joins
inside the long sentence, pauses, intonation and tempo artifacts; record reviewer,
PASS/FAIL and observations. This is the remaining D012 validation gate. D002/D008
release gates are unchanged. D013 has not been started; the branch is unmerged.

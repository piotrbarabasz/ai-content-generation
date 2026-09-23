# D031 — Measured text-audio alignment

## Implemented boundary

D031 adds a separate `SpeechAlignment` artifact beside the D012 sentence/chunk
map. It binds one immutable section revision to one retained `SectionAudio` WAV by
project, section, revision, text checksum, audio artifact/checksum, sample rate and
frame count. It does not replace the user's text or mutate the audio artifact.

Every lexical source word is represented exactly once by its original Python
character range and retained D012 sentence identity. A word has one of three
states:

- `aligned`, with a monotonic measured frame interval and confidence at or above
  the configured threshold;
- `low_confidence`, with the same measured interval but a score below the
  threshold or no provider score;
- `omitted`, with no invented time or confidence.

The artifact records source-word coverage, its combined quality outcome and the
count of provider words that could not be matched to the known text. Validation
rejects missing/duplicate source words, reversed/overlapping/out-of-WAV intervals,
incorrect confidence labels, changed text/audio and inconsistent summary or index
metadata.

## Provider and application flow

`AlignmentProvider` is a small provider-neutral protocol. The one D031 adapter is
`WhisperXAlignmentAdapter`. It receives a managed backend callable and normalizes
WhisperX-compatible `segments[].words[]` output. Unicode-aware known-text tokens
are matched in order using a deterministic longest-common-subsequence mapping;
repeated words remain ordered, source omissions stay explicit and provider
insertions are counted. The adapter never imports WhisperX, loads a model or
downloads assets by itself.

`compose_whisperx_alignment` is opt-in composition for an already managed backend.
`SpeechAlignmentService` verifies the current section and retained D012/WAV bytes,
runs the adapter, converts seconds to PCM frames, validates the complete artifact,
then publishes immutable version-1 JSON. Provider/runtime identity is retained in
artifact metadata. If runtime output, validation or publication fails, no new
registered alignment exists and the preceding history/latest result remains
unchanged.

## Evidence and remaining gate

Offline fixtures cover repeated known text, Unicode-aware source ranges,
punctuation, provider insertions, missing and explicitly untimed words, low
confidence, malformed results, frame monotonicity/WAV bounds, immutable
publication, changed inputs, failure preservation, opt-in composition and project
reopen.

The real-aligner smoke remains pending. It requires an explicitly supplied managed
WhisperX backend/model and authorized narration WAV. The smoke must record backend
and alignment-model versions, language, threshold, word coverage/outcome and a
manual comparison of word and sentence synchronization. Model downloads and
private audio must remain outside the repository. D012's manual prosody comparison
and D025's signed clean-Windows acceptance also remain separate open gates.

Validation on 2026-09-23 (Windows, isolated Python 3.11):

- focused D031/D012/scene-timing checks: 91 passed;
- full `python -m pytest backend/tests -o addopts='' -q -x --tb=short`:
  1671 passed, 11 existing optional tests skipped in 685.42 s;
- `compileall`, `pip check` and `git diff --check`: PASS.

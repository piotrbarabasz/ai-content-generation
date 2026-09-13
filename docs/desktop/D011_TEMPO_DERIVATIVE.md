# D011 — Tempo as an immutable audio derivative

## Scope and composition

Implemented from master `7f5f201` (D010, PR #69), on
`feat/d011-audio-tempo-derivative`. D010's section service, measured media,
artifact store and D040 gate were inspected; the baseline focused suite passed
73 tests before implementation.

`application.section_tempo.SectionTempoService` depends on an injected artifact
port, the D006 coordinator and D040 publication. It has no TTS, database, UI or
provider imports. `storage.section_tempo.SectionTempoArtifacts` adapts the existing
project artifact index/store and `tts.post_processing.process_pcm_wav_tempo`.
There is no additional byte store, selection schema, provider or worker runtime.

```python
artifacts = SectionTempoArtifacts(index, store)
tempo = SectionTempoService(publication, coordinator, artifacts)
queued = tempo.enqueue(section_revision, raw_artifact_id, 1.25)
claim = coordinator.claim_next("tempo")
result = tempo.run(claim)
original = tempo.selected(section_revision, variant="original")
processed = tempo.selected(section_revision, variant="processed")
```

Dispatch only tempo jobs to this service. Run the synchronous service on the
project's owning coordinator outside the UI thread. FFmpeg runs as a separate
hidden child with no shell and a 120-second execution limit; responsive UI/process
cancellation orchestration remains outside this synchronous entry point. Recorded
cancellation is checked before processing and again by D040 before publication.

## Identity and selection

Enqueue requires the current selected **raw** `SectionAudio` for the exact current
section revision. It verifies project ownership, stored checksum and measured PCM
parameters. A processed derivative cannot be supplied as raw, so changes never
compound tempo transforms or restart native synthesis.

The deterministic derivative key hashes strict canonical JSON containing:

- Raw WAV checksum.
- Normalized numeric tempo, validated by the existing 0.5–2.0 contract.
- Processor contract version, currently `ffmpeg-atempo-pcm16-v1`.

The key is persisted in both the D005 request settings and immutable
`audio_derivative` metadata. The request additionally binds the exact consumed raw
artifact ID/checksum and section revision, allowing D040 to reject stale selection.
The processor version describes this application's transformation contract; it is
not a claim of a managed or pinned FFmpeg binary. Binary delivery is later work.

Two separate D040 selections persist:

- `section:<section_id>:audio:raw` — original D010 audio.
- `section:<section_id>:audio:processed` — latest eligible tempo result.

The explicit `original`/`processed` read choice returns a measured `SectionAudio`.
It does not create a job, launch TTS/FFmpeg or modify a selection. Missing/stale
processed media returns `None`, with no silent fallback. A newer raw artifact,
section revision or processor contract makes the old derivative unavailable as a
current processed choice while retaining it in artifact/history records. Both
selection pointers survive reopen; no separate persisted playback-mode preference
or UI selector is introduced.

Changing tempo creates another immutable result while retaining earlier variants.
Repeating the same completed claim is idempotent and launches no processor. A new
explicit job can produce a distinct artifact with the same derivative key; global
deduplication/cache scheduling is not implemented by D011.

## Processing and failures

The existing processor receives verified raw bytes and uses isolated temporary
files beneath configured project `work/tempo` storage. It checks WAV readability,
PCM format, channel count, sample rate, sample width and measured duration against
the requested tempo tolerance. D011 additionally rejects empty output, including
very short inputs for which the processor's duration tolerance alone is insufficient.
Tempo 1.0 retains byte-identical audio without locating or running FFmpeg.

`section_audio` metadata records the output's measured frames/rate/duration and
checksum. `audio_derivative` records the raw reference, key, processor version,
tempo, input/output checksums and measured durations. The exact validated output
bytes and both metadata blocks go through the existing D040 publication service.
Raw audio and unrelated sections are never written by this operation.

Invalid/missing FFmpeg output, process failure, timeout or publication failure
cannot select a new result. Failed attempts are recorded in D006; previously
selected processed media remains intact. A committed D040 result survives a later
journal-cleanup exception. Cancellation prevents selection, and a stale but valid
result is retained historically. Temporary processing files are cleaned on normal
failure; D004 continues to own publication-orphan recovery after a process crash.

## Validation evidence — 2026-09-13

Thirty new offline integration cases cover actual D010 provider call counts,
original/processed choices, history/reopen, immutable raw/unrelated bytes,
tempo-key identity, competing completions, raw replacement, concurrent revision
edit, duplicate completion, cancellation, invalid tempo, corrupted raw input,
FFmpeg output validation, missing output, process failure/timeout, publication
rollback, post-commit cleanup failure and bounded hidden child invocation.

Focused suite: **110 passed in 12.67 s**, including those 30 D011 cases and
existing tempo, D010, D040 and dependency-index regressions.
`python -m pytest backend/tests`: **971 passed in 64.98 s**.
`git diff --check`, new-file whitespace, relative documentation links and task-scope
checks: **PASS**. Only D011 changed in the task backlog; validation used Windows
and isolated Python 3.11.9.

Explicit real PCM fixture smoke:

```powershell
python packaging/d011/smoke.py --output <new-ignored-directory> --ffmpeg <installed-ffmpeg-executable>
```

The executed smoke used FFmpeg **8.1.2**, a 4-second 440 Hz fixture, 22050 Hz,
mono signed 16-bit PCM. Raw SHA-256:
`eea9f11ac0c54f8dedb4780a932785f999c67d18c523e56f05901558bf550fe4`.

| Tempo | Measured frames | Measured seconds | Output SHA-256 |
| --- | --- | --- | --- |
| 0.8 | 110050 | 4.99092970521542 | `14aa4859e78e2709fb643dbabec95a24b43e88626f81df86e6cda3577038ab17` |
| 1.25 | 70650 | 3.204081632653061 | `245522f0f25b5cb3e5a901fb7ee7362b2ea1c144f62e31b8c7baa68835c880a8` |

Both outputs passed the existing measured-duration tolerance. All raw A/B/C bytes
remained identical, both choices survived reopen and the TTS factory was never
loaded. Report: `.tmp/d011-ffmpeg-smoke/report.json` (ignored). No media, binary,
model or machine-specific path is committed.

The existing processor holds one section WAV in memory; this task does not claim
constant-memory processing or a bundled FFmpeg release. No alignment, native TTS
speaking-rate change, UI preference persistence or D012 implementation is included.
D002/D008 release gates remain unchanged.

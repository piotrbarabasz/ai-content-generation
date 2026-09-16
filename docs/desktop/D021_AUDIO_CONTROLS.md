# D021 — Audio controls and playback

## Implemented boundary

The D020 editor includes a dockable audio panel. A configured catalog supplies
compatible built-in voices for the project language and production usage policy.
Reference voices are excluded: intake and approval remain outside D021. Voice
preview is limited to 400 characters and uses native tempo 1.0. Production uses
the identical canonical provider/model/voice/language/settings selection. The
preview provider's effective synthesis identity must equal the D010 voice port's
prepared identity before synthesis or cache reuse is allowed. Existing catalog,
selection validation and preview cache remain authoritative.

The panel provides preview, single-section synthesis, progress, cancellation,
original/tempo-processed playback and stop. Original and processed are explicit
choices; missing processed audio never falls back to raw. D011 derivatives are
played from existing selections; this task does not add a derivative scheduler.
Selected retained recordings whose section revision changed are labeled STALE
and remain playable. Processed audio is also stale when its raw selection or
processor version changed. The label "current revision" describes the editorial
revision, not a claim that every possible future voice/setting dependency is fresh.
Checksums and full PCM measurements are verified before playback. Playback never
modifies publication heads or historical artifacts.

## Thread ownership and lifecycle

A Qt timer advances a bounded asyncio iteration on the GUI/coordinator thread.
D006 queue and D040 publication operations remain on that thread, preserving
SQLite ownership. D007's asynchronous subprocess execution leaves Qt events
running. Runtime/voice preparation and preview synthesis run in worker threads
without project connections. A prepared immutable voice result is supplied to
D010 enqueue, and D010/D040 still validate current expected section identity and
conditional result promotion. Editing remains enabled during generation; a late
result for an edited section remains historical.

The panel owns one operation at a time. Existing queued/running jobs or a paused
queue must be resolved before starting another; this is not a replacement queue
UI or automatic recovery policy. Cancel before enqueue creates no job; cancel
after enqueue uses D006/D007 cleanup. Preview cancellation suppresses playback
and waits for the bounded provider call to return; it cannot forcibly terminate
a third-party in-process provider. Switching projects and closing the editor
wait until audio cleanup finishes. Section selection/editing remains available.
Unsaved text cannot be accidentally submitted as the saved section.

## Explicit composition

The manual-editor entrypoint still works without an audio runtime. In that case
the panel explains that audio services are not configured and disables actions.
There is no hidden mock, download or CPU/provider fallback.

`app.desktop.audio_composition.compose_audio` constructs the existing D006,
D010, D040 and D007 services for an open session. Product composition supplies
catalog, voice/output adapters, trusted worker launch, configured preview root,
preview provider builder, enabled provider IDs and optional synthesis settings:

```python
from app.desktop.__main__ import main
from app.desktop.audio_composition import compose_audio

# All arguments below are explicitly provisioned/injected by host composition.
def audio_factory(session):
    return compose_audio(
        session, catalog=catalog, voices=voices, outputs=outputs, launch=launch,
        preview_root=preview_root, preview_builder=preview_builder,
        providers=("piper",), settings={"piper": {"device": "cpu"}},
    )

main(audio_factory=audio_factory)
```

`voices` and `outputs` may be the same D010 `ManagedSectionAudio` instance.
The preview builder must provide the same effective provider implementation and
bound execution time; private runtime packages must not be assumed present in
the desktop interpreter. A deployed managed preview composition is not silently
invented here. The composition function adds no provider implementation or cache.

## Validation evidence

Validation is in progress. Tests use fake audio/providers, actual Qt events and
async subprocess I/O, and actual temporary D003/D006/D040 databases with synthetic
PCM. No remote API, private recording or real model inference is used.

Windows native playback probe (2026-09-16, CPython 3.11.9, Qt/PySide6 6.11.2):
muted QMediaPlayer/QBuffer playback decoded a synthetic mono PCM WAV at 8000 Hz,
8000 frames, reached 1000 ms and EndOfMedia with no error. The native 1000x720
editor rendering was inspected with the dock visible. Qt logged unavailable HEVC
encoder discovery; WAV decoding succeeded and no HEVC feature is claimed.

Human audible playback in the packaged spike environment is **NOT VERIFIED**.
D002 clean-Windows/manual playback evidence remains deferred; the automated
decoder probe does not close that gate. Before completing D021 acceptance, test
audible preview, original and tempo-processed playback, Stop and repeated
playback with explicitly composed services in the target environment, recording
tester/date/device/build. Production managed-runtime preview composition also
needs its host-specific smoke; the UI/adapter tests use deterministic providers.

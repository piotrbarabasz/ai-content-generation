# D073 Shorts playback and UI state repair

## Scope and baseline

User-selected repair of SHORTS-001/002/004/005/008 from
[the October audit](../../reports/SHORTS_ORIENTATION_AND_ERRORS_AUDIT_2026-10.md).
The checkout was clean on `perf/indexed-artifact-reads` at
`70a104b37a1f3fabcf48012ac5795c71294bc8e3` (the committed audit).
Implementation branch: `fix/shorts-playback-state-consistency`.

The D073 task boundary in `IMPLEMENTATION_PLAN.md` records the inspected
D071/D072/D073 dependencies before implementation. This change preserves the
three-step interface, verified-output export, source/interval validation,
append-only artifacts and editorial decisions. It changes no provider, storage,
workflow-engine or FFmpeg rendering contract. SHORTS-003/006/007 remain separate
work; no production media was regenerated, and no paid provider was invoked.

## Behavior

- `ShortsPlayback` owns the displayed URL, media kind and project/service/candidate
  context. Both panels show the media kind beside the video; original playback
  uses source framing, and filtered stills retain their separate portrait-preview
  label. Ready-card Play chooses the verified Short. Original review remains
  explicitly available in both review and finished-output actions, with Stop
  available in the original review step as well as finished playback. Export still
  resolves the verified artifact selection, never the player URL.
- Each load disconnects earlier request handlers and captures a new generation.
  Readiness callbacks check that generation, the live context and the player URL.
  They never load a file. A pending seek is applied only to loaded, seekable media;
  playback begins only once the requested position is confirmed. Stop cancels
  pending autoplay, replay requests the original interval again, and the verified
  end position pauses playback. Project binding and close invalidate/stop pending
  playback; closing also respects unsaved Director finishing edits.
- Loading/error video is hidden rather than showing an earlier source's frame
  under a new media label. Finished-video canvases have exact 9:16 dimensions
  (117×208 in the compact workspace; 225×400 in Director). Original review switches
  the shared viewport to a landscape shape with explicit source identity. Both
  panels preserve Qt KeepAspectRatio; content is never stretched. Source framing
  can itself be portrait.
- Resource/format/decoder/InvalidMedia failures retain a dedicated error message
  until explicit playback retry or dismissal. Generic ready/status refreshes do
  not erase it. Messages identify original, verified Short, directed Short or
  narration and distinguish unavailable files. A playback error performs no
  artifact invalidation or deletion. Independent operation failures can still
  appear in the main status while the playback diagnostic remains visible.
  Explicit retry clears a failed Qt source before loading its URL again: real
  recovery tests reproduced Qt otherwise ignoring the same restored file URL.
- Director rendering loads the verified new output without autoplay and clears
  any parent interval bound. All exposed finishing controls (burn-in, font,
  headline, duration, branding) create an unsaved finish draft and clear the old
  display. Play/export require saved settings; direct Render saves the finishing
  draft, while script/scene drafts still require their existing review/save.
  Save/discard and current-fingerprint checks determine readiness. Export
  revalidates finish drafts after its file dialog. Prior outputs remain retained.

## Validation

Changed files:

| File | Purpose |
|---|---|
| `backend/app/desktop/shorts_playback.py` | Shared displayed identity, pending seek/cancellation, errors and aspect-preserving viewport |
| `backend/app/desktop/shorts_panel.py` | Ready/original actions, labels, three-step placement and persistent diagnostics |
| `backend/app/desktop/director_panel.py` | Safe playback, verified render loading and finishing draft guards |
| `backend/app/desktop/editor.py` | Stop pending playback on close and identify unsaved finishing edits |
| `backend/tests/integration/test_shorts_playback.py` | Native synthetic playback/timestamp/export/error acceptance |
| `backend/tests/integration/test_shorts_panel.py`, `test_director_panel.py` | State, export, retained history and finishing regressions |
| `backend/tests/shorts_playback_support.py` | Controlled player for non-MP4 fixture state tests |
| `docs/desktop/IMPLEMENTATION_PLAN.md`, this document | Explicit repair scope and verification evidence |

The state tests use a controllable player because their existing artifact fixture
intentionally contains non-MP4 bytes. They check output/original labels and URLs,
ready/unrendered and project changes, stale readiness callbacks, Stop/replay,
ResourceError/FormatError persistence, original-to-directed render transitions,
every finishing control, save/discard/rerender, retained history and export byte
identity (including edits during the save dialog).

`test_shorts_playback.py` separately creates small CPU-only synthetic H.264/AAC
files in pytest temporary storage. Real Qt 6.11.2 `QVideoSink` timestamps verify
the 30–60-second excerpt, rapid selections, Stop/replay and Director's original
preview. It checks end-bound pausing, a portrait source switch, actual atomic
MP4 copying/reopening and real missing/corrupt media errors. It captures frames
during PlayingState after the pending seek is confirmed, rather than asserting
only `setPosition()` arguments. Qt position/frame timing is decoder-granular;
this UI fix does not change the renderer's exact frame/sample boundaries.
The missing/corrupt cases also restore a valid file at the same path and require
successful native decoding on Retry.

Commands (repository Python 3.11 virtual environment):

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/integration/test_shorts_playback.py backend/tests/integration/test_shorts_panel.py backend/tests/integration/test_director_panel.py backend/tests/unit/test_shorts.py backend/tests/unit/test_shorts_selection.py backend/tests/integration/test_shorts_analysis_snapshot.py backend/tests/integration/test_shorts_director.py backend/tests/integration/test_shorts_media.py backend/tests/integration/test_director_media.py --tb=short --junitxml=.tmp/shorts-playback-focused.xml
.venv/Scripts/python.exe -m pytest backend/tests/integration/test_shorts_playback.py backend/tests/integration/test_shorts_panel.py -k 'native or real_missing or readiness_and_seek or simple_layout or playback_error or ready_card_play or pending_callbacks' --tb=short
.venv/Scripts/python.exe -m pytest backend/tests/integration/test_director_panel.py -k decoder_error --tb=short
.venv/Scripts/python.exe -m pytest backend/tests --tb=short --junitxml=.tmp/shorts-playback-backend-verified.xml
git diff --check
```

Focused validation: **138 passed in 543.65 s (9:03)** across all nine listed
Shorts/Director/media suites. The subsequent final acceptance run, including
same-path recovery and the original-review Stop action, passed **15 cases in
13.02 s**. Both Director decoder/retry cases passed in **45.28 s**. Four desktop
layout cases passed at 1360×800 and 1050×850 in **3.13 s**; representative final
offscreen screenshots were visually inspected. Partial development runs are not
counted as full validation.

Final full backend result: **2,636 passed, 12 skipped, 1 failed in 1,610.38 s
(26:50)**, 2,649 cases collected. All **146 Shorts/Director cases** in that run
passed. The sole failure is
`test_duration_repair.py::test_d078_cloud_measurements_drive_duration_repair_without_images`
at test line 432 → `desktop/cloud_audio.py:406`:
`ValueError: Section synthesis incomplete; valid chunks remain available for retry.`
This is the same test and error recorded in the audit baseline and
`D073_SHORTS_UI.md`; neither cloud audio nor that test was changed here.

Final `git diff --check` and new-file whitespace checks passed. Complete logs and
JUnit evidence remain under ignored `.tmp/shorts-playback-*` paths; the full run is
`.tmp/shorts-playback-backend-verified.xml` / `.log`, and final native/state
acceptance is `.tmp/shorts-playback-acceptance-final.log`.

No merge is performed. Native visible-window/audio-device and human editorial
acceptance remain separate from the offline decoder and state tests.

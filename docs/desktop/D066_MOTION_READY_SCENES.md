# D066 — Motion-ready image masters and local cinematic scene motion

## Resolution model

Image generation, motion masters and video delivery have separate dimensions.
Landscape generation remains 640×360 and portrait generation remains 360×640
unless the selected image provider declares different source dimensions. The
delivery profile is FHD, QHD or 4K UHD. A motion master uses exactly 5/4 of the
delivery width and height, preserving 16:9 or 9:16:

| Orientation | Profile | Delivery | Motion master |
| --- | --- | --- | --- |
| Landscape | FHD | 1920×1080 | 2400×1350 |
| Landscape | QHD | 2560×1440 | 3200×1800 |
| Landscape | 4K UHD | 3840×2160 | 4800×2700 |
| Portrait | FHD | 1080×1920 | 1350×2400 |
| Portrait | QHD | 1440×2560 | 1800×3200 |
| Portrait | 4K UHD | 2160×3840 | 2700×4800 |

Square masters are not the default because they waste pixels when the output
orientation is already known. Raster overscan allows subtle motion and safe
cropping; it does not add semantic image detail. Existing provider generation
dimensions are retained.

## Image lineage and upscale

`motion_master` is a new SceneImage provenance with lineage version 3. Its
retained evidence includes the original artifact/checksum/dimensions, delivery
profile and dimensions, motion-master dimensions, 5:4 overscan, native model
scale/output dimensions, resize method and upscaler diagnostics. Historical
lineage v1/v2 and `final` artifacts retain their existing interpretation.

Motion-master preparation resolves derivatives to their retained source before
inference. The local upscaler performs one native x4 pass, followed by a single
Lanczos resize when its native output does not match the master dimensions.
Changing source, delivery profile, dimensions, upscaler identity or resize
policy changes the cache fingerprint. A selected existing image can be made
motion-ready without calling the image-generation provider.

## Prompt and motion policy

Generated visual prompts use request algorithm version 3 and the explicit
`single-scene-motion-v1` policy. It preserves the existing one-scene/one-image
contract and context priority while asking for natural breathing room, a safe
central composition, useful depth separation where appropriate and expendable
edge content. It explicitly prohibits visible guides, frames and crop marks.
Historical prompt revisions remain immutable; a v2 generated prompt is stale
under v3.

Motion masters use deterministic `auto-subtle-v1` motion resolved from scene ID
and policy version. FFmpeg supports zoom in/out and four pan directions, with
maximum zoom 1.10 and crop travel contained by the overscan. Legacy images keep
static rendering. Motion policy is included in final render request identity;
proxy identity also includes the renderer executable and policy.

## Rendering and compatibility

Final render delivery dimensions come from the frozen timeline's selected
motion masters or retained final-image profiles. A mixed profile is rejected.
Timelines without delivery metadata continue to render at the historical
1280×720 size. New final requests use `motion-mp4-v1`; the MP4 stays at 25 FPS,
yuv420p and square pixels, with exact timeline frames and unchanged audio ranges.
Portrait proxy output is 360×640; landscape proxy output is 640×360. Captions
are applied after scene motion.

No Timeline serialization or project database schema migration is required.
There are no persistent per-scene motion controls in D066; manual overrides,
keyframes and custom curves remain deferred.

## Implementation and acceptance evidence

Implemented in this branch: exact dimension helpers; v3 SceneImage model and
motion-master preparation/cache/publication; motion-aware visual prompt policy;
Visuals terminology and manual motion-master action; AutomaticWorkflow motion
master stage; deterministic FFmpeg motion and profile-aware final/proxy sizing;
portrait proxy sizing; render evidence v2; synthetic FHD motion, dimensions,
lineage/reopen, cache reuse and deterministic policy tests. The full backend
suite passed (1,898 passed, 11 skipped) on 2026-09-30.

### Acceptance follow-up (2026-10-07)

The follow-up remains within D066: source/master integrity, cancellation and
reuse, historical render requests and synthetic media acceptance. It adds no
Timeline or project database migration.

Issues corrected:

- Reopening a v3 master previously checked only the master bytes. It now also
  verifies the retained original source checksum, dimensions and accepted-scene
  ownership. Source cycles are rejected, and preparation unwraps chained legacy
  derivatives to their original source before inference.
- The v3 model now enforces the exact profile dimensions, 5:4 overscan, native
  x4 dimensions, consistent resize method and complete upscaler identity. Cache
  fingerprints also explicitly retain source format and dimensions.
- Final/master publication accepted up to 64 MiB while reopening applied the
  16 MiB imported-source limit. Derivatives now use the matching 64 MiB limit;
  imported/generated sources keep their existing limits.
- A worker error after cancellation previously became a blocked workflow.
  Cancellation now keeps its canceled outcome and suppresses late publication,
  including providers without an optional cancellation hook.
- Pending algorithm-v1 render jobs were rejected after D066 introduced v2.
  Exact historical requests are now validated with their original settings and
  use the original static 1280x720 adapter identity. Executable checksums still
  have to match. Current requests use the current motion/delivery behavior.
- The original portrait check measured dimensions without encoding. Its
  replacement encodes, probes and fully decodes an actual 360x640 proxy.

Deterministic acceptance is covered by `test_motion_master_acceptance.py` and
`test_motion_compatibility.py`, plus expanded render/media/caption tests:

| Boundary | Evidence |
| --- | --- |
| Failed master creation | Inference error, truncated PNG, incorrect dimensions and invalid diagnostics preserve the selected valid final and all prior artifact bytes. Private partial files are absent from the artifact catalog. |
| Stale selection | Both prepare/publish and an actual asynchronous worker with an owner-thread selection change reject the late master without registering it. |
| Cancellation and resume | Two-scene persisted project, retained manual prompts/audio/images/finals, real desktop driver and scene/timeline services. A late valid result, worker error and absent cancellation hook each retain the first completed master and leave the second final selected. Reopen creates only the missing master; another run performs no writes. |
| Paid provider reuse | Image-provider spy asserts exactly **0 generation calls** throughout interruption, reopen/resume and repeat. Master inference counts are **2** before interruption, **3** after resume, and still **3** after repeat; the two fixture-final preparations are excluded from those counters. |
| Source/master lineage | Original ID/checksum/dimensions; delivery profile/dimensions; master dimensions; overscan; native x4 dimensions; resize method and provider/model/version/runtime are asserted. Corrupt sources and mismatched lineage fail on read. QHD native-size masters retain `None` resize; detailed 4K PNGs exceeding 16 MiB remain selectable/readable. |
| Historical compatibility | Literal v1/v2 SceneImage manifests, v3 manifests, original D018 Timeline payload and immutable ID, pending historical requests/publication/replay, and real static v2 renders. Historical jobs stay 720p; current requests retain explicit final profiles, with no motion applied to legacy images. |
| Real FFmpeg | FHD landscape movement with distinct first/last frames and no black corner; actual portrait proxy; exact 25 FPS/frame counts/durations and output sizes; reordered nonzero audio sample ranges at 44.1/48 kHz with verified 220/440 Hz output tones; subtitle pixels before/during measured intervals and subtitles applied after motion. All media are synthetic. |
| Determinism/cache | Same scene/policy resolves identically; changing motion policy changes final request and proxy cache identities. Changing original source, profile or provider/model/version/runtime changes the master fingerprint. |

Validation on Windows, isolated Python 3.11, Qt offscreen and local FFmpeg/ffprobe
8.1.2, using only synthetic media and injected offline providers:

- Broad focused regressions: **137 passed in 184.70 s** (master/image publication,
  compatibility, render, captions, ScenePanel, preview and render contract).
- Final expanded master/compatibility/caption regressions: **36 passed in 69.43 s**.
- Full `python -m pytest backend/tests`: **1,943 passed, 11 skipped in 494.32 s**.
  The skips are the existing D044 Windows symlink-privilege cases; no D066 case
  is skipped. This follow-up adds **39 regression cases** to the 1,904-case baseline.
- `git diff --check`: PASS.

Focused reproduction:

```powershell
.\.venv-ci311\Scripts\python.exe -m pytest backend/tests/integration/test_motion_master_acceptance.py backend/tests/unit/test_motion_compatibility.py backend/tests/integration/test_video_render.py backend/tests/integration/test_final_image_presets.py backend/tests/integration/test_image_upscale.py backend/tests/integration/test_ffmpeg_render_media.py backend/tests/integration/test_synchronized_captions.py backend/tests/integration/test_scene_panel.py backend/tests/integration/test_preview.py backend/tests/unit/test_render_contract.py
```

### Desktop and remaining manual gate

The source desktop entry point successfully launched and exited using its
`--release-smoke` path with Qt offscreen and an empty explicit environment file.
This is startup evidence only. Native desktop interaction and audible/visible
playback are not available through the enabled control tools; no interactive,
GPU/upscaler or paid GPT Image acceptance is claimed.

D066 therefore remains **Partial**. The smallest external completion step is one
operator-run acceptance session on the configured Windows/GPU machine:

1. Activate the Python 3.11 development environment and the already installed
   managed TTS/image/upscale runtimes described in
   [environment configuration](ENVIRONMENT_CONFIGURATION.md). Launch
   `python run_test_chatterbox.py` for the configured Chatterbox path, or
   `python -m app.desktop` for the installed/default audio composition. Open the
   local, ignored `acceptance-social-001` workspace (or a private copy retaining
   its project and artifacts). That runtime data is not a source-control fixture.
2. In Storyboard/Visuals select an existing retained landscape image or its final
   derivative. Choose Landscape/FHD and create the motion master. Verify that
   the source ID/checksum stays unchanged, no image-generation call occurs, the
   native x4 pass runs once, and lineage records 1920x1080 delivery,
   2400x1350 master, 5:4 overscan and the measured resize method/diagnostics.
3. Build the timeline, preview and export. Play the final MP4 and listen to the
   narration. Confirm subtle motion, no blank edges, readable captions when
   enabled and correct framing. Probe 1920x1080, square pixels, 25 FPS and the
   exact timeline frame count/audio duration. Close/reopen and run Automatic
   again: confirm retained sources/masters are reused without paid generation.
4. Repeat with a retained portrait source and Portrait/FHD: 1350x2400 master,
   1080x1920 final, and a playable 360x640 portrait proxy. Confirm that selecting
   an old static variant preserves its dimensions and renders without motion.
5. Complete the separately requested paid GPT Image acceptance with one explicit
   image-generation action using the configured provider. Inspect the motion-safe
   composition, make its master locally, and confirm that master creation/resume
   adds zero further image-provider calls. Record actual provider and upscaler
   identities, source/master/output checksums and measured native/resize details.
6. Record operator/date, Windows/GPU/driver, runtime/model identities, output
   probes, visual/audio observations and actual provider-call counts in this
   evidence note. Keep private images, credentials and runtime data outside Git.
   Mark Completed only when this interactive and hardware/provider evidence
   passes alongside the automated evidence.

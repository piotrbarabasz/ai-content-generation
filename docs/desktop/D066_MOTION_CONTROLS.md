# D066: independent Zoom and Pan controls

This is the authorized quality/control follow-up to D066. It adds compact
project-wide controls in Visuals, without per-scene editing or Timeline/schema
changes. D066 remains Partial until its existing hardware/provider gates and
the visual comparison below have actual operator evidence.

## Policy and strengths

The historical `auto-subtle-v1` policy chooses one of six zoom/pan directions,
uses linear progress, zoom up to 1.10, and traverses the whole available pan
crop range. Its behavior is retained for algorithm-v2 render requests.

New builds use `motion-controls-v2`. Zoom and Pan each offer Off, Subtle and
Medium. Defaults are **Zoom Subtle, Pan Off**. Both Off renders a static central
delivery crop, including FHD/QHD/4K masters; Draft is not required.

| Family | Subtle | Medium |
| --- | --- | --- |
| Zoom | 1.000 to 1.035 relative to delivery crop | 1.000 to 1.070 |
| Pan | 25% of minimum available overscan travel | 50% |

The 5:4 master has a base zoom of 1.25 for the central delivery crop. Therefore
FFmpeg zoom is 1.25 to 1.29375/1.3375, or the deterministic reverse. Pan endpoints
are centered at +/-12.5% or +/-25% of the minimum available crop travel. Pan
does not gain extra travel as zoom increases, keeping combined motion bounded.
Each family's direction is independently derived from scene ID, family and
policy version; changing the other family does not reverse it.

The new trajectory uses `t*t*(3-2*t)`. An intermediate raster at twice the
master dimensions reduces integer crop quantization. It stays in 4:4:4 until
after `zoompan`, avoiding its input chroma-grid snapping; explicit `floor`
coordinates and final yuv420p conversion make rounding predictable. No runtime
randomness, direction changes, shake, external motion library or keyframes are
introduced. Quantization remains finite; visual smoothness still needs viewing
at native size. The denser raster also increases CPU/memory use, especially at
4K, which should be measured during operator acceptance.

## Persistence, freshness and compatibility

Explicit control changes append immutable `project_motion_settings` JSON
artifacts through the existing checksummed, cataloged project store. The latest
retained settings are loaded on reopen. Defaults need no stored record, and
opening a project does not write motion preferences. There is no SQLite project
migration, Timeline serialization change or generic settings framework.

AutomaticWorkflow receives the visible/reopened choices and persists them via
the desktop driver. It still stops at a ready timeline; the existing explicit
final-render action and downstream regeneration use the same project settings.
It does not silently add a final encoding stage to AutomaticWorkflow.

New algorithm-v3 requests freeze policy and both intensities in request settings
and renderer identity (`motion-mp4-v2` / `proxy-motion-mp4-v2`). Timeline scene IDs
deterministically bind directions. Motion changes stale only final rendering
and proxies. Script, audio, plans, prompts, images and masters retain their
identities and freshness. Old videos remain retained and can still be inspected
or copied; Export explains when they use older motion settings.

Algorithm-v1 and v2 requests retain their original settings, executable identity,
dimensions and motion semantics when executed or resumed. They are never
rewritten as v3. An explicit new build/preview uses the visible controls; an
existing retained video does not change merely by opening the project. Legacy
static images remain static under every policy. New motion still requires v3
motion-master lineage, exact overscan and the original source checksum.

Each encode freezes configuration before awaiting FFmpeg. A final result whose
project configuration changed meanwhile is retained without replacing the active
selection. A proxy whose configuration changed is not presented as current.
Cache identities include policy, both intensities, timeline and executable.

## Automated evidence

All tests use temporary projects, synthetic media and offline providers:

- `test_scene_motion.py`: defaults, all nine intensity combinations, deterministic
  family directions, landscape/portrait bounds, easing and monotonic optical
  centers (within integer raster quantization), distinct final/proxy fingerprints
  and exact historical v2 identity.
- `test_motion_controls.py`: actual offscreen combos, restart without schema
  change, the editor's AutomaticWorkflow handoff, render-only freshness, late
  result rejection, retained historical replay and proxy invalidation/reuse.
- `test_motion_controls_media.py`: eight real encodes covering static FHD,
  Subtle/Medium zoom, horizontal/vertical pan, Medium pan, combined Subtle and
  portrait FHD. They check exact 20 frames, 25 FPS, delivery dimensions, full
  decoding, visible movement/static output, no blank corner, unchanged nonzero
  source audio range and measured 440 Hz narration.
- Existing FFmpeg/caption checks now exercise the new default, including actual
  portrait proxy output, reordered 44.1/48 kHz audio and captions after motion.
  Historical request tests still exercise the original adapters.
- The existing two-scene AutomaticWorkflow interruption/reopen/resume regression
  additionally changes Zoom/Pan: paid image calls stay **0**, master inference
  calls stay **3**, and selected masters and Timeline identity stay unchanged.

Validation on 2026-10-07, Windows, isolated Python 3.11.9, Qt offscreen and
FFmpeg/ffprobe 8.1.2:

- Final focused policy, controls, plan-driver compatibility, synthetic media and
  caption run: **59 passed in 50.86 s**.
- Separate final control tests: **7 passed in 20.76 s**; eight synthetic motion
  encodes: **8 passed in 11.38 s**.
- Full `python -m pytest backend/tests`: **2,012 passed, 11 skipped in 539.08 s**.
  All 11 skips are the existing D044 Windows symlink-privilege cases. No motion
  tests were skipped. This follow-up adds **40 cases** to the 1,983-case baseline
  and extends the three existing provider-reuse/cancellation cases.
- `git diff --check`: PASS.

The first full run exposed a script-only adapter without the new optional motion
writer. The desktop driver now accepts that existing port; real SceneServices
still persist the exact selected configuration. The focused compatibility run
and complete rerun above passed after that correction.

Reproduce the focused run with:

```powershell
.\.venv-ci311\Scripts\python.exe -m pytest backend/tests/unit/test_scene_motion.py backend/tests/integration/test_motion_controls.py backend/tests/integration/test_motion_controls_media.py backend/tests/integration/test_plan_driven_script.py backend/tests/integration/test_ffmpeg_render_media.py backend/tests/integration/test_synchronized_captions.py
```

## Remaining visual acceptance: same image, same duration

Native desktop inspection is unavailable through the enabled control tools.
No human viewing/playback success is claimed. The smallest remaining check is
one Windows operator session using a private copy of the intended local test
project (`acceptance-social-001`, or the existing operator test project):

1. Open a retained motion-master scene at FHD. Keep the same scene ID, source,
   master, timeline and duration (preferably 5-8 seconds) throughout. Save a
   copy of the current render before rebuilding.
2. In Visuals choose **Zoom Subtle / Pan Off**, rebuild only the final render,
   and save a copy as `zoom-subtle.mp4` outside Git.
3. Choose **Zoom Off / Pan Subtle**, rebuild and save `pan-subtle.mp4`.
4. Choose **Zoom Subtle / Pan Subtle**, rebuild and save `combined-subtle.mp4`.
5. View all three at native delivery size and replay their beginnings/endings.
   Check restrained composition, ease-in/out, no visible shaking/oscillation,
   no blank edges, unchanged narration timing and captions. Confirm no image
   generation/upscale job was added. Reopen and verify the last combo persists.
6. Check a portrait scene/proxy and, if used, 4K encode CPU/memory responsiveness.
   Record operator/date, scene duration, directions, FFmpeg version, checksums
   and visual/audio observations here. Keep generated media outside Git.

Do not claim Completed until this comparison and the broader D066 manual gates
in [motion-ready scenes](D066_MOTION_READY_SCENES.md) are actually demonstrated.

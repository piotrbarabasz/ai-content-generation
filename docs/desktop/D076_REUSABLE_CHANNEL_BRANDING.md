# D076 - Reusable Channel Intro & Outro Library

## Integration base and preserved behavior

Branch: `feat/d076-reusable-channel-branding`. The working tree was clean at
start. Fetched origin/master remained `6477e1a`, with integrated D067-D073.
This feature is stacked on local D075 commit `407bcec`, which is not integrated
into origin/master. No merge was performed. D075 live narrative/TTS acceptance
remains separate and is not claimed by this task.

Inspected D068 `BrandingConfig`, `ProjectBranding`, `VideoFinishingService`,
`FFmpegFinisher` and Export composition. Also inspected D019 retained renders,
D067 narration guard, D070 publishing/caption timing, D071 Shorts mapping and
D073 quality derivation. This extends those contracts; the final documentary
renderer, parent script and Timeline are not rewritten or edited.

Legacy version-1 card configurations and their renderer identity retain their
meaning. Existing logos, settings, branded output history and valid caches remain
readable. New project settings use version 2, `mode: reusable_clips`, with an exact
channel/profile revision and explicit intro/outro/logo version references,
checksums and measured source clip durations. Changing shared defaults requires
an explicit Apply selected channel defaults action in an existing project.

## Shared storage and profiles

The Windows application library is `%LOCALAPPDATA%/AI Content Studio/channels`,
for both source and installed runs, independent of `projects/`. Source/test hosts
without LOCALAPPDATA use the XDG data convention. Tests inject temporary library
roots; the desktop Export panel also accepts an injected library.

`ChannelLibrary` retains immutable profile JSON revisions and asset versions in a
version-1 SQLite catalog. Stable channel and per-channel asset-slot identities are
separate from profile/version IDs. Profiles contain name, tagline, CTA, optional
HTTP(S) link, logo/intro/outro selections, default placement/enabled preferences,
background/accent colors and UTC creation/update timestamps. Only explicit public
metadata fields are accepted; no provider credentials or OAuth tokens are loaded
into profiles. Profile heads use an expected-revision fence under BEGIN IMMEDIATE.
The default channel is an explicit preference, not a mutable input alias in
historical project finishing requests.

Assets record channel/type/asset/version identities, SHA-256, size, measured media
properties, UTC creation time, import or local-generation provenance, validation
results and (for creation) input fingerprint/source version references. Imported
bytes are retained internally and survive deletion of the original import file.
Only enabled clips are required for finishing; an unused intro or the original
logo is not required to play the bytes of an already baked outro.

Intake is bounded before and during copying: logos 20 MiB/4096 pixels per side/
16 million pixels; clips 256 MiB, up to 60 seconds/4K/120 FPS; sounds 32 MiB, up
to 60 seconds. Logos require actual decoded PNG content. Video/audio inputs are
probed and completely decoded with local FFmpeg, with local file/pipe protocols
and a bounded demuxer whitelist. Inputs with unknown/unsupported timing, rotation,
non-square pixels, delayed stream starts, extra streams or unsupported audio
layouts require an actionable re-export. No file-extension-only trust is used.

Storage resolves safe relative paths and rejects traversal, links and Windows
reparse points at operation boundaries using the existing containment helpers.
Files are fsynced and atomically published under exclusive UUID names before the
catalog advertises them. SQLite writes serialize profile/catalog changes. A crash
or cancellation can leave an unselected work file/orphan, but never overwrites a
historical version or selects partial media. This is operation-boundary safety,
not a sandbox against a hostile process concurrently rewriting the whole library.

## One-time creation and audio

ChannelLibraryService supports logo, intro MP4, outro MP4 and optional sound
imports. The local creator reuses D068's card layout: transparent logo, channel
name, tagline, background/accent colors, subscription reminder and two visual
recommendation boxes. Clips have simple entrance/exit fades. Creator defaults are
3 seconds for intro and 15 seconds for outro (2-4 and 10-20 ranges). Supported
creation sizes are 640x360, 1280x720 and 1920x1080 at 25/30 FPS; the desktop uses
1280x720. Higher-resolution finished clips can be imported. No AI, TTS or model
runtime is required.

Optional sound is resampled and trimmed/padded to the created clip duration with
fades; without sound the creator emits a silent AAC track. Identical creator
inputs reuse the validated immutable clip version. Explicit changes to logo,
metadata, sound or render policy create a new version, preserving old bytes.
Finishing never calls the clip creator and never rerenders the logo elements.
Concurrent writes are protected; simultaneous first-time creator requests can
still produce equivalent independent versions, rather than a distributed creation
lease. There is no background regeneration or deletion operation.

## Finishing and verified timing

Reusable finishing reads the exact enabled shared clip paths and verifies their
checksums before and after processing. The original MP4 is staged through D068's
existing immutable-source path and remains byte-identical. Clips are normalized
in the same final filter graph, avoiding a separate intermediate lossy generation.
Output remains H.264/yuv420p, source resolution/FPS, AAC at 48 kHz and source
mono/stereo layout, with reset presentation timestamps and MP4 faststart.
Full decoding, frame counts, stream durations, dimensions/FPS and final checksum
are verified before publication. Failure/cancellation preserves prior outputs.

Fit is explicit: Contain letterboxes without crop/stretch, or Reject requires
matching aspect ratios. Valid clip audio is preserved and converted to the source
layout/rate; absent audio gets exact-duration silence. No audio streams are mixed.
Actual decoded RMS and sample-peak measurements provide a warning for a clip over
6 dB louder than source narration or output near full scale. These are dBFS/RMS
measurements, not invented LUFS or true-peak claims. Review levels or use the
existing optional D069 mastering stage; there is no second normalization system.

After cold open requires D068's verified Standard plan, PlanScriptBinding,
current ordered section revisions, matching Timeline/render snapshot and exact
frame/audio-sample boundary. Missing mapping blocks that placement and the UI
explains that Before documentary can be selected explicitly. The outro always
follows the complete original source, preserving the narrative conclusion.

D067's 540-660-second Standard narration check uses the original source audio;
branding cannot make a short Standard source pass. Source narration, selected clip
durations and estimated normalized final duration are displayed separately.

Inserted durations are quantized to jointly representable source video frames and
48 kHz samples. For example, a measured 1.5s/30 FPS clip inserted into a 25 FPS
source uses 38 frames/72960 samples, or 1.52s. At 30000/1001 FPS, inserted frames
use the necessary frame quantum so sample offsets remain exact. Probe decimals
are checked within microsecond rounding tolerance, not treated as exact rationals.

Every new reusable branded result carries an output-checksum-bound timing map:
source/output frame and sample intervals, insertion frame/sample, intro/outro
version/checksum/normalized duration, final frame/sample counts and duration.
The map must cover the entire original exactly, and match selected assets,
source identity, boundary, FPS and measured output evidence.

D070 publishing timing reads and verifies this map, so chapter/subtitle offsets
use actual inserted durations; source cold-open captions stay in place and later
captions shift only after insertion. D071 already consumes D070 timing for Shorts.
D073 uses the same map and distinguishes silent branding from audio-bearing clips.
Absent/inconsistent maps make timed exports unavailable, not guessed. D069 and
publishing fingerprints follow actual selected derived source identities; changing
branding never automatically rebuilds script, speech, images or publishing assets.

## Desktop workflow and portability

Channel Library is available from the application menu before opening a film and
from Export > Branding / Finishing. Create/select a channel, save metadata/import
logo, import or create clips, preview them, select historical versions and set the
default channel. Long operations run asynchronously, and close/cancel waits for
owned process cleanup. A concurrent profile edit leaves the new asset retained
but requires deliberate version selection instead of replacing a newer head.

In Export choose Reusable channel clips, select the channel and explicitly Apply
its defaults. Enable/disable each clip, choose versions/placement/fit, preview,
build/rebuild, play the validated result or save a copy. Pinned profile identity
and actual durations remain visible. Opening/restoring a project does not append
new settings or apply changed global defaults. Missing shared data remains
explicit and allows Disable or legacy-card fallback. Recommendation boxes are
visual; clickable end screens still require YouTube Studio setup.

Shared assets are local to the user's application library. Project-only copies do
not contain large shared MP4s. Moving to another computer requires the associated
library data with its original IDs/checksums. Back up the entire channels directory
when the application is closed and restore it into an empty destination library,
alongside the project. Do not overwrite a populated destination catalog. A selective
bundle export/import or library-merge UI is not implemented. Missing versions are
reported; another channel's assets are never substituted. Unselected import/render
work files may remain for inspection; no shared asset deletion/cleanup UI is added.

## Validation and acceptance

Deterministic unit/reopen/ownership/override tests inject temporary roots. Real
local FFmpeg tests cover imported audio/no audio, 44.1-to-48 kHz and frame-rate
normalization, contain/reject aspect policies, creation/cache/version retention,
source checksum and acoustic preservation, cancellation, measured level warnings,
rational FPS, captions/chapters/Shorts/QA timing, downstream freshness and desktop
management without a project. The Windows symlink creation test is skipped when
the process lacks the privilege; production containment still rejects reparse paths.

The two-project acceptance creates Documentary Test Channel, one 3-second intro
and 15-second outro, then two tiny 540-second synthetic Standard source fixtures.
These are low-resolution color/tone media, not two AI production runs. Both use
verified plan/script/Timeline mappings, insert after a 20-second cold open and
produce 558-second results. Tests preserve the 540-second source duration and all
original bytes, check subtitle/chapter/Shorts offsets and D073 silence intervals,
update only Project B to intro v2, preserve Project A's exact historical reference,
reopen both projects/library and show no clip recreation during finishing.

Automated playback/preview actions verify selected paths; human on-screen visual
and listening acceptance, especially imported music levels and upscaled clip
appearance, remains separate. No external service or GPU was used.

Final validation on Windows with isolated Python 3.11.9 (`.venv-ci311`) and
Qt offscreen:

- Legacy D068 finishing regressions: **31 passed**.
- Expanded planning/branding/publishing/QA/desktop/duration checks:
  **127 passed, 1 skipped**.
- New shared library, media and desktop checks: **26 passed, 1 skipped**.
- Final missing-library/disabled-component/placement/application-menu/reuse
  regression: **5 passed**.
- Full `python -m pytest backend/tests`: **2389 passed, 12 skipped** in
  **1116.09 seconds (18:36)**. Executed with `.venv-ci311/Scripts/python.exe`.
- D076 task fields, 75 unique backlog headings, dependency acyclicity, M7
  membership and documentation links: PASS.
- Final working-tree/staged whitespace checks: PASS.

The symlink privilege skip does not establish that host's privileged-link
creation behavior; existing containment checks remain active. Human on-screen
visual/listening acceptance is not claimed. Existing project data and all
runtime/model/media output directories are excluded from the commit. The feature
branch is retained for review and is not merged.

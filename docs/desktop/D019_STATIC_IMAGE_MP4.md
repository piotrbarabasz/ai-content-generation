# D019 — Real static-image MP4 renderer

## Boundary and profile

`VideoRenderService` enqueues one D006 job for an exact D018 timeline and uses
D040 to publish validated bytes. The request pins the complete versioned
timeline, all input artifact IDs/checksums, profile and SHA-256 identities of
both trusted FFmpeg executables. Application code imports no concrete provider,
storage, runtime, image decoder or UI.

The fixed profile `static-mp4-720p25-v1` is 1280x720, 25 FPS, H.264/libx264,
yuv420p, CRF 20, veryfast preset, mono AAC 128 kbit/s at 48 kHz and MP4 faststart.
Other timeline timebases are explicitly rejected. The measured `RenderedVideo`
desktop contract is separate from the legacy `VideoRenderingModule` reference
dictionary; existing provider registry/workflow/mock behavior stays compatible.

No UI, transitions, captions, GPU requirement, scene-MP4 intermediates, automatic
cache or automatic retry is introduced. Explicit enqueue creates a new
job/generation; completion replay returns its committed result.

## Sources and timing

`ProjectVideoRender` resolves immutable historical inputs by project-owned IDs,
checks D013 acceptance/timing and D016 image selection history, verifies source
bytes and stages private copies in configured `work/render/<unique-id>` storage.
D044 checks each path boundary. Original images/WAVs are preserved. Derived
PNG inputs apply EXIF orientation and composite alpha onto black. FFmpeg applies
aspect-preserving fit with black borders or centered fill/crop.

Visual clips use D018's integer frame counts and simple cuts. Narration is
trimmed by original half-open sample ranges **before** derived resampling to
48 kHz mono. Audio and visual sequences concatenate independently. There is no
`-shortest` or audio trimming to rounded video boundaries. Narration can outlast
video by D018's half-frame quantization difference. Audio duration tolerance is
one 1024-sample AAC frame plus one output sample per resampled clip. Neither
normalization nor encoding overwrites source media.

## Process ownership

`MediaProcess` runs native FFmpeg/ffprobe asynchronously, with shell-free
arguments, hidden Windows windows, stdout bounded to 1 MiB, a 64 KiB stderr tail,
an explicit deadline and cancellation polling. It reuses D007's Windows
kill-on-close containment and ownership principle: queue writes and publication
remain on the owning coordinator, while heavy media work runs in child
processes. Native FFmpeg does not use the Python-worker JSON handshake.
Cancellation, timeout, malformed output and nonzero exits stop and reap the
child, including cancellation of the awaiting task.

Await `VideoRenderService.run()` on the owning project's event loop; do not
transfer its SQLite session to a worker thread. Input verification/staging is
synchronous local I/O; D019 does not claim UI-latency-free staging for arbitrarily
large projects. Failed private work directories remain for diagnosis; retention
and cleanup policy is not expanded here.

## Validation and publication

Encoding must exit zero. ffprobe counts decoded frames and checks exactly two
streams, MP4, dimensions/codec/pixel format, 25 FPS, exact video frame count and
duration, AAC/48 kHz/mono and narration duration tolerance. A second FFmpeg
invocation fully decodes **both** streams with error escalation. A URI, extension,
success message or probe alone is insufficient.

MP4 checksum/size must remain stable during validation, when opening the output
and at catalog registration. Encode/probe/decode failure, cancellation, changed
executables and source/output corruption cannot replace the prior render.

`RenderResultIndex` scopes D040 freshness checks to the explicit D018/D016
selections. It does not mistake newer generated candidate heads or old prompt
freshness for the user's image choice. This is needed to render a deliberately
selected older image variant; all consumed artifact checksums remain mandatory.
Late section/image changes or superseding render jobs retain valid MP4 results
without promoting them. The final comparison occurs inside D040's publication
transaction, including changes immediately before the transaction.

## Composition

```python
from pathlib import Path
from app.application.result_publication import ResultPublicationService
from app.application.video_render import VideoRenderService
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.ffmpeg_render import FFmpegRenderer
from app.storage.local_store import LocalArtifactStore
from app.storage.video_render import ProjectVideoRender, RenderResultIndex

jobs = JobRepository(session.repository)
index = RenderResultIndex(session.repository, jobs)
store = LocalArtifactStore(index.root, index=index)
coordinator = JobCoordinator(jobs)
service = VideoRenderService(
    ResultPublicationService(index, store), coordinator,
    ProjectVideoRender(index, store),
    FFmpegRenderer(Path(ffmpeg_executable), Path(ffprobe_executable)),
)
attempt = service.enqueue(timeline)
claim = coordinator.claim_next("desktop-render")
result = await service.run(claim)
```

Executable paths are trusted composition, never job JSON. Disabled rendering
does not instantiate or import FFmpeg. Managed binary distribution, licensing
and installer integration remain in their dedicated tasks.

## Validation evidence

Status: **PASS**, 2026-09-15, Windows / isolated Python 3.11.9.

Real smoke uses synthetic colors and tones only,
without AI, network, private reference audio or model downloads. It requires
`ffmpeg` and `ffprobe` on PATH and fails rather than skips if either is missing.
Local tools are Gyan FFmpeg/ffprobe **8.1.2-full_build**; real jobs pin executable
content hashes. CI must supply the tools and pass the same actual media smoke.

- Dependency baseline: **98 passed in 19.96 s** (D018 domain, D040 publication
  and legacy video module tests).
- Focused process/contract/publication/real-media suite: **37 passed in 177.09 s**.
- Final contract check after aligning raw-audio dependency keys: **3 passed in
  0.95 s**. Final real publication/reopen and work-junction checks: **3 passed in
  24.12 s**. There are **38 new D019 cases** overall, including the added junction
  case; later checks refine existing cases rather than duplicate the suite count.
- Full `python -m pytest backend/tests`: **1371 passed, 11 skipped in 696.23 s**.
  All skips are existing D044 Windows symlink privilege cases (`WinError 1314`);
  no D019 test is skipped. Both acceptance criteria PASS.
- `pip check` and `git diff --check`: PASS. All 13 changed/new files pass
  UTF-8/whitespace/EOF checks. Backlog checks confirm 57 valid tasks with acyclic
  dependencies, consistent milestones and 32 P0 tasks in the release closure.
  Only the D019 backlog block changes. Changed-document links/anchors resolve.
- Branch `feat/d019-static-image-mp4`; no commit, push, merge or D020 work.

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Playable MP4 with expected audio/video profile and durations | Actual FFmpeg encodes both fit/fill fixtures, ffprobe counts frames and measures streams, both streams fully decode, immutable published bytes survive project reopen. Separate red/blue and 220/440 Hz fixtures verify cuts, exact source ranges, mixed 44.1/48 kHz normalization and reversed order. | PASS |
| Nonzero exit/truncation never publishes success | Encode/probe/decode failures, malformed JSON, wrong profile/count/duration, canceled jobs and corrupted source/output bytes leave the prior result unchanged. A real MP4 truncated to half its size fails validation. Catalog checksum validation catches alteration during publication. | PASS |

Additional tests cover late section/image changes, superseded jobs, the final
pre-commit selection window, explicit old generated images, completion replay,
transaction rollback, truthful post-commit cleanup outcomes, executable identity
drift, redirected work paths, bounded process pipes, deadlines, cancellation and
event-loop responsiveness. The application imports without concrete media/runtime
dependencies. No original media or approval/selection history is overwritten.

## Changed files

- `backend/app/domain/render_result.py`
- `backend/app/application/video_render.py`
- `backend/app/providers/ffmpeg_render.py`
- `backend/app/runtime/media_process.py`
- `backend/app/storage/video_render.py`
- `backend/app/modules/video_rendering.py` (legacy/desktop contract documentation)
- `backend/tests/unit/test_media_process.py`
- `backend/tests/unit/test_render_contract.py`
- `backend/tests/integration/test_video_render.py`
- `backend/tests/integration/test_ffmpeg_render_media.py`
- `docs/architecture/provider-contracts.md`
- `docs/desktop/IMPLEMENTATION_PLAN.md`
- `docs/desktop/D019_STATIC_IMAGE_MP4.md`

Focused reproduction:

```text
python -m pytest backend/tests/unit/test_media_process.py backend/tests/unit/test_render_contract.py backend/tests/integration/test_video_render.py backend/tests/integration/test_ffmpeg_render_media.py
```

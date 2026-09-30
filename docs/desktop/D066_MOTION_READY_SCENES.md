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

Validation still required before marking D066 complete: motion-master failure
and stale-selection coverage; an existing-project AutomaticWorkflow assertion
that counts zero paid image calls; a final review of legacy render payload
readers; interactive acceptance using `acceptance-social-001`; and final
`git diff --check`. The requested real local upscaler/hardware run and paid GPT Image
manual run are not represented by synthetic tests. No project database,
generated media or provider credentials belong in the implementation commit.

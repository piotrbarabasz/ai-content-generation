# D070 — YouTube Publishing Package

## Verified base and integration

The original checkout was clean on `fix/standard-video-plan-structure-contract`,
HEAD `c9e1890`. Fetch verified `origin/master` at `92c1432`. Local D067 `4ba1efe`,
D068 `f0a5e51` and D069 `d8e5c95` were present. The dedicated
`feat/d070-youtube-publishing-package` worktree is stacked on D069; no dependency
was inferred complete from its task number or merged automatically.

The desktop Publish tab composes the existing project store and selected render,
Video Plan revisions, plan/script bindings and immutable Timeline. Original eligibility
requires verified MP4 bytes, decoded render evidence, matching current Timeline,
saved script revisions, motion settings and media bindings. Branded eligibility uses
D068's current fingerprint/byte verifier. Delivery eligibility uses D069's current
settings/source fingerprint and byte verifier. Historical/stale/preview/cache files
are never adopted as sources. A delivery REVIEW remains visibly REVIEW; package READY
does not certify the source's loudness or editorial quality.

Source verification is deferred until opening Publish or explicitly refreshing it.
Expensive media-binding verification is reused only while upstream artifact IDs/
checksums, selection heads, Timeline and saved script remain unchanged; MP4 bytes are
rechecked at action boundaries. Publishing revisions do not invalidate that media
check or regenerate upstream assets. The original acceptance project was never
opened for writing.

The legacy `PublishingModule`, `PublishingRequest` and YouTube adapter were inspected.
They implement an approved network upload/localization handoff, not a local desktop
package. D070 deliberately never constructs/invokes them and needs no OAuth or
YouTube credentials. Their existing tests/contracts remain unchanged.

## Metadata and retained review

Explicit Generate uses the editor's injected configured LLM and its existing
`generate_structured` contract. Context includes working title/topic, full Film Brief,
Plan/format, saved script/revision, included Timeline sections/sample spans,
language, actual selected duration, optional
channel name and explicitly configured links. The strict response requires exactly
three distinct title styles, description, keywords, three hashtag suggestions and
optional pinned comment. Candidate sets retain context, provenance and provider
identity separately from editable/approved revisions. Generation never silently
replaces existing manual metadata. Loading a candidate is a deliberate replace action.

Without a provider, Generate creates a labeled **deterministic starting draft, not
AI**, from the working title and included Timeline section titles. Planned Film-Brief
runtime/coverage claims are kept in context, not copied into that draft. The description is bounded to 4,000
UTF-8 bytes while the complete source context remains retained. This draft promises
neither stylistic differentiation equivalent to an LLM nor factual verification.

Machine checks reject malformed schema, duplicate/oversized titles, unconfigured
URLs, unsupported numeric tokens introduced by LLM output and generated chapter
timestamps. Title/description reject angle brackets; the assembled description must
fit 5,000 UTF-8 bytes. Tags fit 500 combined characters, including separators and
quotes around multiword tags. These conservative package checks follow the
[YouTube metadata specification](https://developers.google.com/youtube/v3/docs/videos).
No application can prove all claims or title/content relevance through these checks.
The user must review accuracy, tone, sources, licensing, source status and thumbnails.

Every saved review/selection is a new immutable artifact with parent revision ID.
Edits clear UI approval and block packaging until saved and reviewed. Switching source
preserves text and prior approvals in history, marks the current draft STALE, and
requires an explicit review/rebase for the selected context. Timed SRT selection is
cleared by rebase; chapter text is preserved for manual correction/review. Candidate,
thumbnail, subtitle, draft and package histories survive restart.

Loading a metadata candidate preserves unsaved chapter/thumbnail edits and the source
selection. Restored previews show the selected thumbnail rather than its base image.
Frame-backed thumbnails from a different video identity and deselected scene-image
thumbnails are STALE until explicitly regenerated or replaced with a custom import.

## Chapters and subtitles

Chapters never come from an LLM. Standard group/section IDs are mapped through the
current plan/script binding to actual Timeline clip starts. Contiguous groups can be
removed, reordered or trimmed: observed Timeline order and duration govern boundaries.
Repeated/split or unknown/stale mappings fail with REVIEW REQUIRED. Manual chapter
correction is allowed. The first chapter is 00:00, at least three markers are required,
all markers ascend strictly, titles are distinct and every span (including the final
one) is at least ten seconds within the actual video. Standard packages require valid
chapters; Social chapters are optional/not applicable.

D068 has no separate composition-map artifact. D070 derives a piecewise map from
verified retained D068 source ID/checksum, exact intro/outro settings and cold-open
audio-sample/video-frame boundary, checking duration/frame count and the Timeline
boundary. A short intro is included in the first narrative chapter, not a separate
three-second chapter. D069 timing must match its underlying source's frame count,
frame rate and video duration. Missing/inconsistent evidence blocks timed output.

`ProjectSpeechAlignments`, `SpeechAlignmentService`, `ProjectCaptionTracks`,
`SynchronizedCaptionService`, `build_synchronized_caption_track`, and the existing SRT
serializer are reused. Caption creation requires exact current section text/revisions,
audio IDs/checksums and complete measured alignment. Matching synchronized tracks and
UTF-8 SRT artifacts are reused. D070 creates a separate adapted SRT when branding adds
silent cards; original caption artifacts are unchanged. A segment crossing an inserted
card boundary is blocked. No proportional/fake timing, new caption model, transcription
or translation is introduced.

Missing alignment has an actionable status. Explicit Generate missing alignment is
enabled only with an injected configured alignment service. `compose_publishing`
accepts that existing D031 service; `ProjectEditor.publishing_factory` supports managed
composition. The current installed application has no automatic alignment backend;
D070 does not install/download one. Provider work runs off the SQLite owner thread;
validated publication stays on the owner thread. Cancellation waits for in-flight
provider cleanup and retains previously completed alignments/candidates.

## Thumbnails and ZIPs

Thumbnail sources are three actual verified-video frame suggestions, current selected
scene images or a bounded explicit image import. Exact CFR frame positions use accurate
FFmpeg seeking; real tests compare it with frame-index extraction. Actual previews
remain selectable: the application makes no promise that arbitrary positions are
representative. Three deterministic Pillow layouts provide headline, font size,
position, darkening, accent and optional verified channel logo. RGB JPEG output is
16:9, at most 1920×1080 and bounded by source quality. Fit/letterbox preserves geometry;
no artificial detail or AI image provider is used. Selected settings/assets are retained.

ZIP entries live under `publishing-package/`: byte-identical `video.mp4`, selected
`thumbnail.jpg`, description, metadata JSON and README, plus available validated
chapters, tags, pinned comment and `subtitles.<language>.srt`. Missing optional components
are recorded in metadata/README. There are no empty SRT/chapter/tag placeholders.
Verified chapters/hashtags are added to the final description before validating its
limit. Metadata records source identity/checksum, actual duration, language, selected/
alternative titles, chapters/tags/hashtags/comment, thumbnail/subtitle checksums,
candidate/approved revision, dependencies, validation/source status and timestamp.

Safe fixed filenames, private staging, fixed ZIP entry ordering/timestamps, CRC checks,
stream checksums and atomic store publication protect the package. Identity binds exact
source, Timeline/context, chapter list, caption track, thumbnail, approved revision and
algorithm. Identical requests reuse validated packages; damaged histories are retained
and rebuilt explicitly. Save-copy actions reuse existing QSaveFile/path/checksum checks
with added ZIP/SRT extensions. No database migration or upstream regeneration is needed.

## Validation evidence

Focused tests cover schema/provenance/errors, manual revision retention, exact trimmed/
reordered chapters, branding/delivery timing, missing/stale alignment, Unicode SRT,
thumbnail dimensions/cache/selection, optional omissions, ZIP/checksum/corruption,
source staleness, cancellation/publication fences and restart recovery. Offscreen Qt
tests exercise deliberate generation/review, previews, SRT/ZIP copying and stale-source
blocking. Existing editor, export, branding, delivery, captions and legacy publishing
regressions are included. Real synthetic CPU FFmpeg frames require no external APIs.
The final focused regression run passed **94 tests** in 87.32 seconds; the
shell/editor follow-up passed **26 tests**. The final `python -m pytest backend/tests`
run passed **2,171 tests**, with **11 skipped**, in 666.30 seconds.
`git diff --check` passed. Offscreen visual inspection confirmed the selected
thumbnail preview and readable review controls with the acceptance snapshot's data.

Acceptance used an isolated full-project snapshot of `acceptance-standard-10min-001`:
426 artifacts / 277,879,108 bytes were copied and checksum verified from read-only
databases. The actual selected original is 1920×1080, 25 FPS, 489.44 seconds. Nine
chapters passed at 00:00, 01:01, 01:55, 02:55, 03:46, 04:35, 05:28, 06:26 and 07:26.
Three real frame previews and three 1920×1080 thumbnail layouts were produced; the
cinematic candidate was visually inspected. The ZIP was built, extracted and all
entries inspected; its copied MP4 matches the selected checksum. Identical rebuild
reused the package, a title-only change rebuilt publishing output while retaining
video/thumbnails, and closing/reopening recovered the approved selection/history.

The real project lacks measured word alignment for 28 narration sections. SRT was
explicitly omitted with that status, not fabricated. Acceptance used deterministic
metadata and simulated editorial approval, not a paid LLM or YouTube call. No branded/
delivery artifact exists in that original project; separate integration tests cover
those sources. Both original database checksums and all 426 upstream artifact
checksums remained unchanged. Real configured LLM/alignment-provider and interactive
editorial/player/YouTube Studio acceptance remain manual gates.

## Changed files

- Core: `backend/app/application/publishing_package.py`,
  `backend/app/domain/publishing_package.py`, `backend/app/storage/publishing_package.py`.
- Local media: `backend/app/providers/local_thumbnail.py`.
- Desktop: `backend/app/desktop/publish_panel.py`, `publishing_composition.py`,
  `editor.py` and the backward-compatible `artifact_export.py` extension.
- Tests: `backend/tests/unit/test_publishing_package.py`,
  `backend/tests/integration/test_publishing_package.py`, `test_publish_panel.py`,
  `test_thumbnail_media.py` and existing `test_project_editor.py` / `test_project_shell.py`
  tab assertions.
- Documentation: this file and `docs/desktop/IMPLEMENTATION_PLAN.md`.

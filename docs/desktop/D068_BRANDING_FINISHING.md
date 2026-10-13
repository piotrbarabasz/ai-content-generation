# D068 — Branding & Video Finishing MVP

## Integration

Finishing is a separate optional service: selected verified final MP4 → local
intro/outro cards → branded MP4. FFmpegRenderer, Timeline schemas, script, speech,
scene/image generation and original render selection are unchanged. The Export tab
retains its original actions and adds a collapsible, scrollable finishing section.
The feature branch is based on D067 (`4ba1efe`), which is not merged in the verified
origin/master (`92c1432`). It does not include the separate local plan-fix/data commit.

BrandingConfig independently enables a 2–4-second intro (default 3) and 10–20-second
outro (default 15). PNG alpha is preserved; Pillow composites the logo over a dark
background and renders white text with a restrained accent. Local Segoe UI or
DejaVu Sans is used when available, with Pillow fallback. The selected font checksum
and Pillow version are part of the rendering identity. Text shrinks to safe margins;
unfittable text produces an error rather than clipping. No fonts or providers are
downloaded. The two recommendation boxes are optional, visual placeholders only.
Clickable end-screen elements belong in YouTube Studio; see [YouTube's end-screen
guide](https://support.google.com/youtube/answer/6388789?hl=en).

## Verified boundaries and media

Before-video placement works independently of plan mapping. After-cold-open requires
the current Standard plan, its active script binding, all ordered current section
identities, the current verified Timeline and the exact source render Timeline
snapshot. Reordered, incomplete, ambiguous or stale mapping disables that option.
The insertion uses the confirmed final cold-open video frame and integral 48 kHz
audio sample boundary. A nonintegral boundary is rejected rather than guessed.

The finisher uses trusted media executable resolution and MediaProcess cancellation.
It preserves all main frames in order and concatenates main audio separately from
video, splitting only at verified frame/sample positions. Cards receive explicit
silent audio and restrained fades. Output is H.264/yuv420p with 48 kHz AAC, preserving
source dimensions, rate and mono/stereo channels. Independent FFprobe checks cover
streams, dimensions, frame count and durations; every output fully decodes before
publication. Expected duration is source duration plus enabled card durations, with
one frame plus normal AAC packet tolerance. Source bytes remain immutable.

## Persistence and recovery

ProjectBranding uses existing atomic project artifact publication for immutable
branding settings, normalized PNG logos and branded videos. Current settings follow
the existing retained-preference history pattern; matching branded output is resolved
by its complete input fingerprint. Identity covers source artifact/checksum, all
settings, logo identity/checksum, placement boundary and renderer/font/executable
identity. Same valid inputs reuse output; changed preferences/source invalidate only
finishing. Damaged cache entries remain historical and are rebuilt, not reused.

Each build stages a verified source copy in a unique contained work directory.
Cancellation, process failure or changed inputs prevent result publication and keep
the original. Failed work directories are retained for diagnosis; retry starts a
fresh encode. Branded play/copy actions revalidate current inputs and output bytes;
copies use QSaveFile and cannot target the immutable artifact directory. No database
migration or changes to original-render selection are required.

Standard narration and branded delivery durations are shown separately. A selected
Standard plan requires the original source's measured audio duration to pass its
540–660-second profile before finishing; cards cannot hide a failed duration check.

## Validation and limitations

Tests use fake processes, temporary projects, offscreen Qt and short synthetic
FFmpeg clips with distinct colors/tones. They cover optional cards/order/duration,
mapping failures, transparency, cache/staleness, cancellation, retained preferences,
play/copy actions, original preservation, and 1920×1080 at 30 FPS. Card previews and
the expanded Export layout were visually inspected.

Validation on 2026-10-12 with the existing isolated Python 3.11 environment:

- Final focused unit, GUI, existing Export/editor and synthetic media run: **61 passed**.
- Synthetic media cases included in that run: **5 passed**, including distinct
  before/after-cut tones, original-frame color order, silent card audio and 1080p30.
- `python -m pytest backend/tests`: **2,077 passed, 11 skipped** in 736.68 seconds.
- `git diff --check`: passed.

Interactive acceptance with the real 10-minute project and a user-selected logo
remains pending; no acceptance project was opened or modified. A standard desktop
player's interactive playback is also pending; full decoding and OS-player dispatch
are covered offline. The MVP supports the application's verified H.264/48 kHz AAC
source profile, not arbitrary uploaded videos. It re-encodes the main film (quality
tradeoff) to permit frame-accurate insertion. Encoding resumes by cache/retry rather
than partially encoded frame checkpoints. No publishing, music or AI branding is added.

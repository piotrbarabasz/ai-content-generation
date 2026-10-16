# D078 — OpenAI narration

The normal desktop entry point is `python run_app.py` (or the installed
`ai-content-studio` command). Install Python 3.11+ in a virtual environment with
`python -m pip install -e ".[dev]"`. Copy `.env.example` to your ignored `.env` and
set `OPENAI_API_KEY`. Optional `AICS_OPENAI_TTS_API_KEY_ENV` names another secret
variable; `AICS_OPENAI_TTS_MODEL` sets the initial model for new projects.
No API request occurs at launch or when saving settings. Credentials are resolved
at dispatch and never enter immutable requests, artifacts or cost history.

## Editing and approval

In Audio, save project defaults or apply settings to selected/all sections. Model,
voice, native speaking speed, style, preset, pacing and custom instructions are
revisioned separately from the editorial script. Per-section overrides win over
project defaults. Save changes before previewing. Speed defaults to 1.00x with
0.05 steps, supports 0.25–4.00x, and recommends 0.75–1.25x. Speed cannot promise a
particular duration. Post-processing tempo remains a separate audio variant.

Preview voice uses short editable text; Preview selected section uses the entire
section. Compare A/B offers retained historical and new preview audio. To compare
delivery settings, save one variant, preview, change/save settings, then preview
again and select both retained entries. Approve preferred B publishes a full
section preview only when its source text/model still match; this uses cached
chunks and cannot issue a fresh paid request. Standalone voice samples cannot be
approved as full section audio.

Every paid operation shows text/chunk counts, expected duration, selected model,
sections to regenerate/reuse and a cost range. Approve an estimated-spend ceiling
and one or two attempts per chunk. Automatic shows an aggregate preflight before
narration and another approval after editorial repair changes the required audio.
Changing a setting never starts paid regeneration. An optional project ceiling
includes all reservations, including failed/uncertain attempts. Usage history
shows reservations and received/failed outcomes; these are estimates, not invoices.

Pricing is versioned in `backend/app/storage/narration.py`, verified 2026-10-16
against [official pricing](https://developers.openai.com/api/docs/pricing):
text input $0.60 and audio output $12 per million tokens. Duration estimates use
2.2 words/sec at 1x, a 0.5–2x duration range and 12.5–50 audio tokens/sec. These
assumptions are deliberately broad and are not a published per-minute rate or a
guaranteed upper bill. Input token upper bounds use UTF-8 bytes. Paid dispatch
stops when this pricing configuration expires (2026-11-16); maintainers must
verify rates, update its version/date and release the update. SDK retries are
disabled. Each allowed application retry reserves estimated spend before dispatch;
a lost response can still cause duplicate billing. New approvals do not reset the
two-attempt limit for the same immutable chunk input. Exhausted operations require
review instead of an unbounded retry loop.

## Compatibility and recovery

The Speech adapter requests WAV with the official Python SDK. PCM normalization
produces 24 kHz mono signed 16-bit WAV. Received bytes are saved before decoding,
with a checksum and immutable response archive. A valid response cache or archive
can recover a crash before the cache pointer or chunk manifest was saved. Verified chunks, immutable identities,
source boundaries and publication gates retain the existing section-audio contract.
Incomplete or clipped audio is rejected, not selected. No crossfade or tempo
change is silently applied. Actual frame counts alone determine timing.

Cancellation is cooperative between streamed blocks/chunks. A request already
in flight may remain billable; the configured 30-second network timeout bounds a
stalled network read. Processing and network work run off the Qt owner thread;
project writes and publication stay on the owner thread. Resume retries missing
work with the remaining approval, reusing received valid chunks. HTTP errors are
categorized without logging provider response bodies or credentials.

Old projects open in retained-audio mode. Play their original WAV without any
Chatterbox installation. Use Migrate explicitly to OpenAI before replacement;
this enables new synthesis and invalidates readiness until newly approved audio
exists. Original selections/artifacts remain in history. Audio changes invalidate
scene timing/timelines while reusable images and completed renders remain stored.
D067 still blocks narration outside Standard 540–660 seconds; D077 uses measured
frames for bounded editorial repair. Voice speed is never silently changed.

`run_test_chatterbox.py` is now a deprecated alias of the normal launcher.
Optional legacy providers/provisioning tools remain available for explicit
maintenance; the product does not load them or verify their models. Nothing
removes installed runtimes or caches. To reclaim space manually, first close the
app and back up projects/reference audio; remove only your explicitly identified
legacy installation/model/cache directories. Preserve project artifact folders
and any assets still needed by separate legacy tools.

## Model lifetime and future adapter

The initial Speech models are `gpt-4o-mini-tts` and its supported 2025-03-20 and
2025-12-15 snapshots. All 13 built-in voices are offered; cedar, marin and onyx
are listed first. See [Speech guide](https://developers.openai.com/api/docs/guides/text-to-speech)
and [model limits](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts).
Requests enforce 4096 characters and conservatively remain below 2000 input
tokens, including compiled delivery instructions. Overlong sentence chunks or
instructions are rejected before any section request is dispatched.

[Deprecations](https://developers.openai.com/api/docs/deprecations) announce
January 6, 2027 shutdown. The UI displays this date, and paid dispatch fails at
shutdown rather than falling back. `gpt-realtime-2.1-mini` is visibly disabled.
The [Realtime API](https://developers.openai.com/api/docs/guides/realtime) uses
stateful sessions/events and different transports. A future adapter must validate
voice/delivery/speed capabilities, negotiate/decode its native audio, publish the
same measured PCM result and provide a new effective identity. Realtime speed,
WAV output and instruction semantics are not assumed to match Speech. Updating
that provider/composition and capability list preserves desktop settings/history,
durable chunk/publication services and the provider-neutral Automatic engine;
unsupported controls must be disabled or explicitly migrated. No Realtime calls
or hidden fallback are implemented in D078.

## Verification

Default tests are offline and use synthetic WAV with a mocked official-SDK-shaped
transport. Run `python -m pytest backend/tests` and `git diff --check`.
Focused narration/Automatic/desktop checks passed, including archive recovery
without a cache pointer. The complete unit suite passed: 1532 passed, 12 skipped
(2026-10-16, isolated Python 3.11 environment).
The complete backend run finished with 2525 passed, 12 skipped and one stale
static assertion that listed only three catalog adapters. After adding OpenAI to
that expected registration list (and the provider-neutrality guard), all 19
static checks passed. Product code was unchanged by this correction. Thus every
collected non-skipped check passed across the full run and static rerun; a second
complete media run was not repeated for the assertion-only update.
The final archive-recovery/Audio/Automatic focused run passed 59 checks; catalog
API/selection regressions passed 36; isolated worker/provisioning coverage passed
54. `git diff --check` and the normal launcher smoke passed.
The opt-in smoke command first supports an offline estimate:

```powershell
python -m app.tooling.openai_speech_smoke --voice cedar --speed 0.95
# Only run after reviewing the estimate; new workspace, one paid attempt/chunk:
python -m app.tooling.openai_speech_smoke --live --voice cedar --speed 0.95 --budget-usd 0.05 --workspace .tmp/my-speech-smoke
```

Safe-copy America 1776 acceptance, 2026-10-16:

- The original project was closed before a consistent SQLite/media copy into
  `.tmp/oab`. All 717 original files still match their saved SHA-256 hashes.
- Cedar, native speed 0.95x, Cinematic style, Cinematic historical preset and
  Measured pacing. The approved $0.05 preview measured 19.659042 seconds
  (471817 frames at 24 kHz); two chunks. Approving it as production audio reused
  the cached responses with no additional paid requests.
- User separately approved a $2.10 estimated-spend ceiling for the other 17
  sections, maximum two attempts/chunk, excluding LLM repair, images and rendering.
  All 18 sections completed with 92 total request reservations, one per unique
  chunk, and $1.033005 reserved estimated spend across both approvals. This is
  not a measured invoice charge. New process reopening reused all 18 sections.
- Measured narration totals **831.231167 seconds**. D067 correctly reports
  **BLOCKED**, target 600, allowed 540–660; at least 171.231167 seconds must be
  removed. D077 measured the cloud artifacts and retained an offline six-section
  editorial repair proposal (expected reduction 170.326150 seconds, not a
  guarantee). No new LLM repair requests were made. This copy is deliberately
  not certified as a new 10-minute export; executing the proposal requires
  separately authorized editorial work and newly estimated narration.
- Before retiming, all current scene candidates rejected the older audio timing.
  Explicitly retiming 18 sections produced 63 valid current timeline candidates
  with the same selected images. The existing timeline was retained rather than
  silently rebuilt; no new images or renders were generated.
- All original artifacts remain indexed, all 18 historical raw WAVs remain
  decodable, and the prior completed 606.76-second video and backup remain stored.

Local evidence: `.tmp/oab/d078-preview-result.json`,
`d078-generation-result.json`, `d078-acceptance-result.json` and
`d078-original-proof.json`; preview audio is `d078-preview.wav`. These private
project outputs and caches are ignored by Git. No project text, credentials or
generated media are committed.

D079 adds chunk eligibility preflight and one explicitly reviewed exceptional attempt
after the original two reservations. See [recovery and operator steps](D079_NARRATION_RECOVERY.md);
ordinary budget approvals still cannot reset the lifetime attempt count.

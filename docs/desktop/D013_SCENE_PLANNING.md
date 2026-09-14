# D013 — Semantic scene planning

D013 uses the implemented D001 section identities and D012 source/sample maps.
D012 remains Partial for its outstanding manual prosody comparison; its automated
boundary contract is available and verified. D013 does not change that status.

## Implemented contract

`ProjectRenderScene` is an immutable project-owned scene value beside the existing
run-based `RenderScene`. It records stable scene identity, exact section revision,
original text range, whole sentence IDs and a visual description initialized from
the selected source text. `ScenePlan` retains the ordered scene selection and
validates unique ownership and complete source coverage. It is scoped to one
section; callers compose sections in the accepted script order.

`SceneTiming` contains sample intervals for a scene. Its immutable containing
`SceneTimingSet` binds every interval to one exact audio artifact/checksum, sample
rate, total frames, accepted plan identity, and D012 method/quality. Coverage
starts at zero, is contiguous and ends at the measured WAV frame count. There is
no conversion to rounded second cuts or estimated word timings.

`ScenePlanningService` provides three separate commands:

- `suggest(section, audio=None, semantic_breaks=())` saves a new proposal.
- `accept(plan_id, reviewer_id=...)` retains an explicit acceptance of that exact
  proposal, with its own immutable identity and reviewer attribution.
- `retime(acceptance_id, section, audio)` produces and retains only a new timing
  set. It does not call suggestion or create/change scenes or visual descriptions.

The service imports only the domain and standard library. An injected sentence
source reader reuses D012's deterministic parser; the storage adapter validates
project ownership, the current section and registered WAV bytes. No provider,
worker, image-generation or UI dependency is introduced into the application.

## Meaning before duration

The MVP interprets paragraph boundaries and explicitly supplied editorial topic
breaks as semantic group boundaries. Editorial breaks are IDs of complete
sentences, not arbitrary character or time positions. These boundaries outrank
duration and are never merged solely to reach a target. This is a deterministic
editorial heuristic, not a claim of model-based understanding of topic changes
inside an unmarked paragraph.

Without audio, suggestion groups whole sentences within those semantic groups;
it creates no guessed duration. With verified mapped audio, dynamic programming
chooses whole-sentence groups with a soft preference for 8–12 seconds, then ten
seconds within that interval. It avoids an unnecessarily tiny final scene. A
coherent 13-second group can be preferred over two 6.5-second scenes. A short
paragraph remains short, and one 35-second sentence remains a complete scene even
if TTS synthesized it in six technical chunks.

Measured duration can inform a new **explicit** proposal. It cannot silently
replace accepted semantics. Scene IDs and source ranges are independent of
technical chunk IDs, voice settings and tempo. Source spans include separating
whitespace and cover the exact original section once.

## Retiming and retained identity

Retiming requires an explicit retained acceptance ID and audio mapped to the same
section revision. Every accepted sentence ID/range must match the D012 map; ranges
are taken from the first/last measured sentence boundaries of each existing scene.
Changed voice duration or changed technical chunk limits leave those scenes intact.
Processed tempo carries D012's `approximate_internal_positions` quality instead
of claiming exact internal alignment. Its full endpoint is still the measured
processed frame count.

Visual assets/prompts are future tasks and are not generated or replaced here.
Keeping the same scene IDs and visual descriptions preserves references keyed by
those identities. A text edit requires an explicit new proposal/acceptance; the
old plan, acceptance and timing remain inspectable historical artifacts.

## Persistence and compatibility

`ProjectScenePlans` uses the configured project artifact store and its existing
SQLite index, with three versioned immutable JSON artifact types:
`desktop_scene_plan`, `desktop_scene_acceptance`, `desktop_scene_timing`.
It does not add tables, a queue, an active-plan pointer, or an automatic approval
policy. Proposals and acceptance histories can be discovered after reopen using
the stable section ID. The caller explicitly chooses the acceptance to use;
there is no implicit latest-plan fallback.

Reads verify checksums and exact retained identities. Commands reject foreign or
stale sections, unregistered/corrupt audio, missing maps, incompatible sentence
identities and altered accepted proposals before publishing new results. Existing
D004 file/index publication and orphan recovery semantics apply: failed index
publication cannot advertise an acceptance. Historical proposals and acceptance
records are never overwritten or deleted.

Legacy `RenderScene.create` and `ScenePlanningModule` remain unchanged and keep
their existing estimated workflow payloads. The new desktop service is explicit;
it does not relabel legacy estimates as measured scene timing. D012 legacy audio
without a map is readable elsewhere but cannot supply D013 measured timing.

## Validation evidence — 2026-09-14

- Baseline D001/D012 and legacy compatibility: **91 passed**.
- Focused suite: **131 passed in 7.31 s**, including **34 new D013 cases**.
- `python -m pytest backend/tests`: **1041 passed in 73.04 s**.
- `git diff --check`, new-file whitespace/EOF/UTF-8 and relative documentation
  links: **PASS**. All 11 changed/new files belong to D013; task-block comparison
  with HEAD confirms no other backlog task changed.
- Synthetic variable-duration tests verify complete text/WAV coverage, 8/12/13 s
  grouping, short semantic groups, very long sentences, editorial/paragraph
  boundaries, immutable values, JSON roundtrip and malformed-input rejection.
- Actual D012 generation and D011 tempo service integration uses deterministic
  offline provider/process fixtures and real project/WAV/artifact code. Retiming
  and reopen preserve plan, acceptance, scene IDs, descriptions, source audio and
  earlier timing bytes. No new TTS call is involved in retiming.
- Voice-change tests preserve an accepted two-scene plan while measured sentence
  durations change from 4/4/4/4 to 10/11/12/13 seconds. A separately requested new
  proposal can contain four scenes without altering the prior acceptance.
- Storage tests cover proposal/acceptance discovery after reopen, revision edits,
  cross-project rejection, corrupt audio/proposal bytes and failed index writes.
- Fresh-process application import excludes providers, TTS, SQLite, storage and UI.
  Legacy scene-planning, voiceover/caption and video-render tests remain passing.

Focused reproduction:

```powershell
python -m pytest backend/tests/unit/test_scene_planning.py backend/tests/integration/test_scene_plans.py backend/tests/unit/test_editorial_revisions.py backend/tests/unit/test_speech_boundary.py backend/tests/integration/test_speech_boundary.py backend/tests/unit/test_t026.py backend/tests/unit/test_t027.py backend/tests/unit/test_t028.py
```

Only D013's backlog entry is updated by this task. D002/D008/D012 pending manual
gates remain unchanged. No D014 implementation, commit, push or merge is included.

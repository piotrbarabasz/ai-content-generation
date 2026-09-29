# D064 — Plan-driven script generation

D064 connects the selected D063 Video Plan to the existing script revision and
sequential media pipeline. Social and Standard both generate one plan group per
structured provider request. Standard long-form generation therefore commits a
cold open, introduction, each chapter group and conclusion independently rather
than requesting an entire film in one response.

## Identity, state and recovery

The stable editorial `section_id` is `narrative_segment_` plus the canonical
content fingerprint of `{version: 1, project_id, planned_section_id}`. Every
generated or edited `SectionRevision.id` remains a fresh immutable revision ID.
An immutable `PlanScriptBinding` artifact ties a project, selected plan revision
and script ID together. A separate compare-and-select event chain retains binding
history without changing `project.sqlite` schema version 1.

Progress is derived from deterministic planned section identities in the active
`ScriptRevision`. A completed group is never requested again; manual edits to its
section revision remain selected. An unbound nonempty script or an active binding
for another selected plan blocks continuation until the user explicitly starts a
new script from the plan. A partially present group blocks with a consistency
error.

Generated group results are retained as versioned, checksummed artifacts after
strict output and word-budget validation. The cache identity fingerprints the
exact plan and group budgets, language and sanitized effective provider identity.
If the application stops after validation but before the script commit, Resume
reuses that group result. Each group commit saves a new ordered `ScriptRevision`
through the existing compare-and-select project repository transaction.

## Provider contract and word guidance

The provider returns only `{sections: [{planned_section_id, text}]}` in the exact
expected order. The dedicated strict schema rejects unknown properties. The
application validates all identities, order, text and an approximate ±25% word
count tolerance before caching or committing the group. The plan remains the
authority for section identity, title, role, duration and word target.

Provider calls stay sequential. Automatic workflow cancellation is checked between
groups and forwarded to the active provider when it supports cancellation.
Completed group commits survive project reopen; a valid uncommitted response can
remain in the cache for Resume.

## Automatic workflow and scenes

When a plan is selected, automatic workflow validates its prerequisites, fills
only missing Film Brief or Visual Style values from that plan, resumes script
groups, then uses the existing Voice, Scenes, Timing, prompt, image, final image and
Timeline stages. Existing prompt context wins wherever it already exists. Projects
without a plan retain the legacy saved-script workflow. Legacy automatic runs now
check image generation, context and requested upscale prerequisites before Voice.

Scene pacing is a soft profile passed into the existing whole-sentence dynamic
programming planner. Social uses 4/6/8-second min/ideal/max targets; Standard uses
8/10/12 seconds. Existing unprofiled plans retain their v1 payload and behavior.
Profiled proposals use ScenePlan payload version 2; accepted plan and timing
history continue to embed and read the exact historical proposal.

Pipeline diagnostics report the selected format, duration and group completion.
Once all current narration is measured, a total outside the selected profile is
reported as REVIEW; D064 does not rewrite the narration automatically.

## Deferred work

D065 owns the broader editor-shell redesign. D064 adds only the Plan-panel controls
for group progress, Resume, and explicitly confirmed script replacement.

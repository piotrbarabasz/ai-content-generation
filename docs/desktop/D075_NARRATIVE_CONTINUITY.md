# D075 - Narrative Structure & Story Continuity

## Verified integration boundary

Implemented on `feat/d075-narrative-structure-continuity` from clean master
`6477e1a`, equal to fetched origin/master. The base contains integrated D067-D073
and the subsequent legacy-reference and project-storage fixes. No D074 implementation
was found or assumed. D063/D064 plan generation, immutable plan selection,
plan/script binding, retained group caches and D001/D003 section editing were
inspected before implementation. D067 validates measured selected narration before
visual production. D068 maps both historical `opening` and `cold_open` group
sections to the finishing boundary; this mapping is preserved.

## Implemented behavior

New Standard plans follow `cold_open`, `introduction`, five to seven `chapter`
groups, then `conclusion`. The structured planning output requires editorial
metadata: central question, narrative promise, hook goal, introduction goal,
ordered chapter contributions/dependencies, conclusion goal and final takeaway.
These describe intended storytelling, not evidence or research citations.

The compatibility policy is `standard-narrative-v2`, an optional extension to the
existing version-1 plan payload. Old plans without that extension use the original
deserialization rules and retain their original order and section identities.
Social planning and its cache identity retain their original contract.

Recommended midpoint allocations reserve 20s for the hook, 45s for introduction
and 55s for conclusion. Chapters receive the remainder by largest remainder
allocation using their weights; sections receive their group's allocation.
Durations sum exactly to the selected 540-660s target. Structures that cannot
allocate a positive duration per section are rejected with an actionable message.
Historical allocations remain readable. Word budgets remain estimates at 2.2
words/second; they do not bypass D067's actual measured-TTS guardrail.

The existing group generator now uses `narrative-context-v2` for Standard. It
receives the outline, metadata, brief, previous retained section revision IDs,
text fingerprints, opening/ending excerpts, preceding actual ending, literal
terminology candidates, question excerpts and next planned contribution. Hook
and introduction material is included for conclusion generation. Each section's
head/tail extraction is bounded to 60 words each; the immediately preceding ending
to 100 words; hook/introduction excerpts to 200 words per section. Terminology
candidates and question excerpts are bounded to 80 and 20 respectively. Excerpts
are explicitly partial evidence. They do not pretend to identify every claim or
prove that a question is unresolved. There is no paid summarization operation.

Conclusion generation requires every preceding planned group to be complete,
including during partial resume. Cache identity binds exact preceding revisions,
plan, group budgets, language, provider/model and policy. Old version-1 generated
artifacts remain readable; Standard caches under the older policy are not reused
as if they had consumed new continuity context. Social cache behavior is preserved.

Explicit regeneration uses a fresh request identity and replaces only the selected
group with child section revisions in a new immutable script snapshot. Other
section revisions and manual edits remain selected. Normal Resume skips completed
groups. Cancellation retains validated output and earlier committed groups.
Generation receipts bind exact output revisions to exact source revisions. Receipts
are written before script selection; an interrupted commit can leave an inspectable
orphan receipt, which does not match any active output revision and grants no approval.
Changes to preceding text mark later context as stale without rewriting text or
introducing media dependency edges. Missing provenance is labelled unverified.

`NarrativeContinuityService` separates deterministic structure/coverage/order,
word/duration metadata and freshness checks from optional AI editorial findings.
Review and approval records are checksummed append-only project artifacts in the
configured store. Group approval binds exact text revisions and survives changes
to other groups. Approvals remain visible alongside stale continuity status.
Whole review snapshots become stale when their plan or script revision changes.

AI review is invoked only by the explicit paid-review action using the configured
structured provider. Findings must have a literal supporting excerpt and exact
supplied group/section/revision references. Literary judgments have severity
`REVIEW`; invalid references or technical-error severity are rejected. Provider
identity, prompt fingerprint and available provider usage evidence are retained;
no cost is invented when the provider supplies no usage. An edited-during-call
response remains inspectable but stale. Review never rewrites or approves text.

The existing Plan panel uses a scrollable content area to preserve the desktop
768-pixel minimum-height contract. It includes group selection, read-only retained-text preview,
role/duration/word budgets, generation and approval/context status, deterministic
Review Narrative, explicit paid AI review, findings, selected-group regeneration
and selected-text approval. The existing Script editor remains the editing surface.
Manual edits refresh the narrative overview. Opening a project only reads records;
it performs no paid narrative review or summary calls. Branding stays separate.

## Validation and acceptance limits

Focused offline tests cover canonical ordering, duplicate/missing/misordered groups,
legacy loading, role allocation, retained predecessor text, revision-bound caches,
conclusion completeness, selective regeneration, approval preservation, source
referenced editorial findings, missing providers, checksums, desktop actions,
no paid calls on reopen and D067 measured PCM validation.

The automated `acceptance-narrative-standard-001` creates an isolated new project
about a worldwide 24-hour internet outage at 600 seconds, generates every group
with deterministic fake providers, reviews it, deliberately weakens and replaces
the hook, preserves approved later sections, restarts and resumes without new
calls. It produces no images and does not modify existing project data.

This fixture validates application behavior, not literary quality. It does not
claim that repeated fixture words resolve the narrative promise. Measured PCM
regressions confirm that 489s remains BLOCKED despite a 600s word budget and
separate branding durations. Real configured LLM editorial acceptance, local TTS
voice generation and listening/measured-duration acceptance remain pending; no
external API or GPU is required by the automated suite. There is no mandatory
AI summarization, automatic fact-checking or automatic correction of findings.

## Live acceptance attempt (2026-10-13)

A new retained workspace was created at
`projects/acceptance-narrative-standard-001` using the configured project-location
resolver. The configured OpenAI `gpt-5-nano` provider had credentials available.
A real Standard planning request for the supplied internet-outage topic timed out
with `OpenAITransportError: OpenAI request timed out.` The smoke request was
bounded to 45 seconds with no transport retries. No plan revision was published;
no script, narration, review or image generation was performed after the timeout.
The initial project was retained for deliberate retry and an ignored local report
was saved as `d075-acceptance-report.json` inside it. Existing projects and provider
configuration were not changed. A subsequent read confirmed zero retained plan
revisions and zero script sections. The default managed Piper composition also
reported no active runtime; no runtime/model provisioning was performed. This
attempt does not establish live narrative quality or measured TTS acceptance.

## Automated validation

- Isolated Windows Python 3.11.9 (`.venv-ci311`), Qt offscreen.
- Initial focused planning/script/duration regressions: 69 passed.
- Expanded focused planning/script/narrative/desktop run: 51 passed.
- Final narrative/desktop run, including media freshness and legacy conclusion
  regressions: 25 passed.
- Final desktop shell and narrative-control regression after the compact-layout
  correction: 12 passed.
- D075 task fields, unique task headings, dependency chain, M7 membership and
  documentation links: PASS.

- Final `python -m pytest backend/tests`: **2363 passed, 11 skipped** in
  993.19 seconds (16:33). The full run uses `.venv-ci311/Scripts/python.exe`.
- Final `git diff --check` and staged new-file whitespace check: PASS.
- Existing project data, ignored acceptance artifacts, credentials, runtime/model
  caches and generated media are excluded from the commit.

The task remains Partial only for live narrative quality, real TTS measurement and
human editorial/listening acceptance. Implementation and offline validation are
complete; the feature branch is not merged.

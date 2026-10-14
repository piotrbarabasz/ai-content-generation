# D064 reliability repair with retained D075 continuity

## Cause and boundary

Integrated base `186d97b` includes D075's narrative context, review records and
selective regeneration (`407bcec`, merged through #104). The working tree also
contained an unrelated Plan-panel progress refresh; that change is preserved.

V1 generation requested an unconstrained `planned_section_id` string, while
application validation required exact IDs and order. Thus a valid plan could fail
when a model copied IDs incorrectly. Group completion checked identities, without
detecting copied editorial objectives. Prompts did not clearly distinguish plan
purposes from final spoken text. OpenAI already owned bounded transport retries;
adding another network retry loop in Automatic would replay exhausted requests.

This repair is within D064, preserving D075 context and D067's measured Standard
narration gate. It changes neither database nor immutable artifact formats.

## Response, cache and publication

New requests use `urn:aics:planned-script-group:v2`: `sections` is a strict object
with required `section_1` through `section_N` string properties and no extras.
The application reads these keys in canonical order and assigns verified plan IDs.
Missing/extra slots, V1 arrays, IDs masquerading as narration, empty text and exact
duplicate section prose are rejected. Semantic relevance still requires review;
deterministic checks cannot establish factual or literary quality.

V2 generation fingerprints include the fixed-slot contract and spoken-prose policy.
V1 keys are calculated separately for historical cache lookup, and historical
artifact sections still pass their exact-ID/order V1 validator. Old payloads are
never parsed as V2. Cached results receive the narration check before selection.
Plan, provider, retained context and revision-conflict fences remain in force.

## Narration and retry behavior

The prompt requests final spoken documentary narration, explains how to use plan
purposes, prohibits editorial/visual/stage directions, and retains D075's context,
transitions, assumptions and factual caution. Illustrations are expressly examples.
Before caching or committing, conservative checks reject exact copied purposes,
heading-only/missing prose, production labels, stage directions and explicit
instruction prefixes. Existing 25% word tolerance remains unchanged. Rhetorical
questions, quoted speech and ordinary viewer imperatives have regression coverage.

A rejected response receives at most one complete replacement request, with its
specific validation reason. No partial response is published. Transport retries
remain exclusively in the provider (`maxRetries + 1` requests per invocation,
configured delay/Retry-After capped at 60 seconds). Exhausted transport,
authentication/configuration and cancellation errors never trigger a group replay.
At most two structured invocations occur per group when content needs repair;
each can use its own configured transport allowance. There is no workflow-level
transport retry loop. Request/group attempts and categories are logged separately.
Cancellation interrupts backoff and prevents another request; an in-flight HTTP
call can still finish or be billed. Run / Resume resets cancellation for a new run.

Logs and UI errors include group title, category, group attempt, section count,
preservation, retry decision and next Resume action. Transport exhaustion keeps
the original exception as its cause. Credentials and full prompts are not logged.

## Saved text and recovery

`narration_findings(plan)` inspects selected revisions without writing anything.
Findings include group, section, revision, reason and explicit corrective action.
The Plan panel displays them on opening/refresh; deterministic narrative review
also includes them. Automatic completes pending groups, then checks every selected
section before any TTS, including manual additions. It blocks with actionable
findings even when historical groups are complete or approved. No saved text is
silently rewritten. Existing explicit selected-group regeneration preserves other
revisions, old snapshots and approval history. Standard images remain downstream
of complete, valid, measured narration duration.

## Offline acceptance, 2026-10-14

After the user closed the source project, a read-only SQLite backup and copied
artifacts were used in a separate ignored workspace under `.tmp`. The original
project's file checksums were compared before and after and were unchanged.

America 1776 Alternate History started with 3/9 groups and six selected sections.
The fake provider returned an incorrect slot for Chapter 2, then a complete valid
replacement. Seven fake calls completed the six pending groups in canonical order:
9/9 groups, eighteen unique sections. The six original section revisions remained
identical. Restart and Resume made no further calls or duplicates. Both cold-open
sections were flagged as copied plan purposes/editorial instructions, with exact
saved revision IDs and selective-regeneration advice. Their text stayed intact,
and the pre-TTS gate blocked it. Zero paid, TTS or image requests were made.
Fake narration verifies contract and spoken-prose handling, not real LLM quality.

Regression coverage lives in `test_script_reliability.py`, the planned-script and
OpenAI unit tests, Plan-panel tests, and existing D064/D067/D075 workflow tests.
The focused run passed 99 tests across those seven test files. Following the
provider-free import correction, the import-boundary, provider and reliability
checks passed together (23 tests). The safe-copy acceptance also passed.

Final full suite: **2,427 passed, 12 skipped** in **1,196.43 seconds (19:56)**,
using `.venv-ci311/Scripts/python.exe -m pytest backend/tests --basetemp .tmp/pt1`.
The short temporary path avoids Windows path-length limits in deep artifact
publication fixtures. Both staged and working-tree `git diff --check` passed.
Neutral error types live in `app.domain`, preserving the editor's provider-free
import boundary.

## Changed-file summary

- `planned_script_schema.py`, `planned_script_generation.py`: V2 slots, V1 cache
  compatibility, source-plan verification, narration validation and group repair.
- `narration_quality.py`, `llm_errors.py`, `openai_llm.py`: deterministic leakage
  checks, neutral error categories, strict duplicate-field parsing, bounded
  transport diagnostics and interruptible cancellation/backoff.
- `pipeline_driver.py`, `automatic_workflow.py`, `video_plan_panel.py`,
  `narrative_continuity.py`: asynchronous repair, actionable UI/reporting and the
  before-TTS gate over current saved revisions.
- Five updated test files and the new `test_script_reliability.py`: offline contract,
  provider, GUI, preservation, recovery and media-gate regressions.
- `IMPLEMENTATION_PLAN.md` and this evidence note: D064 task boundary, root causes,
  contracts, acceptance and limitations.

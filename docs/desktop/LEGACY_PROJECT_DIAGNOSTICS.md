# Legacy project diagnostics and missing section revision

## Verified state and root cause

Inspected on 2026-10-13. The main worktree was on `master` at `2251b1c`
(D067-D073 integration merge), with existing user changes to `jobs.sqlite` and
untracked project work. Those changes were preserved. The isolated implementation
worktree is `D:/Projects/ai-content-generation-legacy-diagnostics`, branch
`fix/legacy-project-diagnostics`, based on that master commit.

`TimelineServices.current_candidate_diagnostics()` eagerly validated every retained
timing before iterating active sections. Timing validation resolves acceptance,
plan and `ProjectRepository.get_section(plan.revision_id)`. A missing historical
section therefore raised KeyError even when the active script was empty and that
timing was irrelevant. Scene proposal/acceptance enumeration and desktop timing
selection had the same validate-before-filter pattern.

Project history independently enumerated complete prompt/image histories and
resolved section provenance before creating choices. One inaccessible historical
source could abort the browser and hide healthy script snapshots. Repository
script history was also eagerly decoded as a single tuple.

## Read-only integrity findings

The complete machine-readable inspection is retained locally in
`.tmp/legacy-integrity/report-final.json`. The inspection uses SQLite `mode=ro`,
`query_only=ON`, checksums and domain Timeline decoding. It does not open a writable
ProjectSession, migrate, recover staging, select a script or call providers.

| Inspection | Observed result |
| --- | --- |
| Project | `project_872392a608e446aa84c0a9995336dbdd` |
| Active script revision | `script_revision_642112bbae9841e6acc9a5be8235d29e` |
| Active sections | 0 |
| Retained script snapshots in current database | 1, the empty initial snapshot |
| Retained section revisions in current database | 0 |
| Complete nonempty snapshots in current database | None |
| Missing identity | `section_revision_f9af39f742c54d1fab03ed8f92f0f8f7` genuinely absent |
| Selected Timeline | Structurally valid frozen `timeline_45756bf5a3156c70e9c5cd6b61cb40ed02e358b210f538a3afc815632640a8ed`; 28 source section revisions unavailable |
| Selected original video | `artifact_db6e7cbf03e94324957aef11d88ace98`, valid retained bytes and completed-render evidence |
| Original SHA-256 | `a6c440eff6531aea46f500982234e17c2fe9298260ce6d884db6c0631323d5bc` |
| D069 output | `artifact_7f2424f170d04eb7a1b243fbad061238`, valid retained bytes/evidence |
| D069 SHA-256 | `a975f0f4ca9faf492188eb73c386c94e9b0a43dfcf04c2680196828eafe810b0` |

The current `project.sqlite` is byte-identical to the empty database tracked in
master. Git checkpoint `c9e1890` contains a different exact database with 10 script
snapshots and 28 section revisions, including the missing identity. Both preserved
D073 and D072 acceptance copies also contain those 10 snapshots/28 revisions.
All 10 snapshots in each copy passed the existing SectionRevision/ScriptRevision
domain validators; the largest snapshot contains all 28 retained sections.
Thus the database and later retained artifact catalog describe different stages
of the project. The inspected state is consistent with checking out the older
tracked SQLite file; the inspection does not establish which operation replaced
it. SQL foreign-key checking alone cannot discover missing JSON artifact sources.

These cases are distinct: the active snapshot is a valid empty draft; there is
no complete snapshot to select inside the current database; older complete source
data exists in verified copies/Git; retained artifacts reference revisions absent
from the current database. No automatic restoration was performed.

The specific missing revision is referenced by:

- Plan `artifact_cb3ea15599cc4e88b7fe484bea579b64`.
- Acceptance `artifact_0a6db6782e744b0f89e0081f25d4cc82`.
- Timing `artifact_7e3149c8440342aaa0fd637bfced7f10`, through its retained plan.
- Prompt revisions `artifact_352f2488df264d7bae983e644efaefbc` and
  `artifact_85acfc3836ea4b4ca364bfa4ce86e00a`.
- The selected Timeline `artifact_59c3620496214d549769fd0b443a335e`, plus related
  audio/image/render metadata and retained QA context. The JSON report lists all
  directly and indirectly identified references.

No current script scene is selected. Historical scene dependencies are unavailable;
the selected frozen Timeline also has unavailable source bindings. Its valid
payload and the already rendered MP4 do not prove readiness to generate a new film.
The original and delivery MP4 retain their independent completed-output contracts.

## Fix boundary

Current candidate enumeration first establishes active revision/accepted-plan
relevance. Structural history headers are read individually, with full ownership,
checksum, source-text and resolver validation retained for matching current inputs.
Missing/corrupt current or unattributable dependencies are rejected; there is no
silent fallback to an older valid current timing. Diagnostics explicitly request
historical warnings, which are excluded from current candidate readiness counts.
The automatic workflow's default current-candidate contract stays current-only.

Scene services use revision-scoped proposal/acceptance and acceptance-scoped
timing enumeration. New scene artifacts retain routing scope metadata; old
artifacts remain readable through their original payloads. This extra related
call-site change is necessary to prevent scene inspection from reintroducing the
same historical error before Timeline diagnosis.

History validates prompt/image/result entries individually. Unavailable entries
remain visible with source identity, failure reason and backup guidance. Script
snapshots are loaded individually, so damaged media or another broken snapshot
does not hide healthy snapshots. Existing restoration checks remain in force;
unavailable choices cannot be restored, even if the caller edits the displayed
availability field. The dialog disables their Restore button. Current inputs are
never fabricated and original JSON references are never rewritten.

The existing Diagnose summary displays the number of unavailable historical
references and one exact actionable example; the full list is in the report,
console and Project history. Existing selected-video availability is retained.

## Inspection and explicit recovery

From the repository with the isolated Python environment:

```powershell
$env:PYTHONPATH = 'backend'
python -m app.application.project_integrity `
  'D:/Projects/ai-content-generation/projects/acceptance-standard-10min-001' `
  --missing-revision section_revision_f9af39f742c54d1fab03ed8f92f0f8f7 `
  --backup 'D:/Projects/ai-content-generation-d073/.tmp/d073-acceptance/project' `
  --output integrity-report.json
```

The report is created exclusively and cannot overwrite an existing file. If an
open desktop session excludes SQLite readers, close that session before inspecting.
The identified recovery candidates are:

- `D:/Projects/ai-content-generation-d073/.tmp/d073-acceptance/project`.
- `D:/Projects/ai-content-generation-d072/.tmp/d072-acceptance/project`.
- Exact Git object `c9e1890:projects/acceptance-standard-10min-001/project.sqlite`;
  an inspection-only copy is `.tmp/legacy-integrity/reference-git-c9e1890.sqlite`.

Keep recovery explicit: inspect a separate verified copy and its script history
first. Complete source data cannot be selected from a database in which it is
absent. This change never installs a backup over the current database or imports
guessed sections. Existing healthy snapshots remain explicitly restorable through
Project history, as demonstrated by regression tests.

## Validation

Focused original timeline/history/diagnostics regressions: 32 passed. Expanded
history/media/legacy suite: 47 passed in 58.95 seconds. Latest legacy/scene-services/
scene-plan/diagnostics run: 33 passed in 47.52 seconds. These suites overlap.
Final `python -m pytest backend/tests`: **2330 passed, 11 skipped in 1082.32
seconds**. The existing optional skips do not constitute hardware/provider
acceptance. `git diff --check` and the staged whitespace check passed.

Acceptance used a new verified copy of all 446 registered project artifacts and
read-only SQLite backups of project/jobs/index. Diagnosis returned SCRIPT BLOCKED,
0 current Timeline candidates, 28 actionable history warnings and retained Export
READY. With the real retained Standard plan, group progress is pending and
narration duration remains pending; no sections or speech duration were invented.

Project history enumerated 311 choices, 310 unavailable due to lost provenance,
with the healthy empty script still accessible. Original and D069 MP4s were probed
and fully decoded with real local CPU FFmpeg, each yielding 12,236 frames.
All copy database bytes, source heads, artifact counts and MP4 checksums remained
unchanged. Original project/jobs/index SHA-256 values also remained unchanged.
No paid generation, media regeneration or source reconstruction occurred.

Changed product files: application `history.py`, `pipeline_diagnostics.py`, new
`project_integrity.py`; desktop `history_dialog.py`, `scene_services.py`,
`timeline_composition.py`; storage `history.py`, `project_repository.py`,
`scene_plans.py`. Regression coverage is in
`backend/tests/integration/test_legacy_project_diagnostics.py`.

No automatic merge, database replacement or project-data deletion is part of this
fix.

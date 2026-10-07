# Project durability: D033, D042, D047

This work implements the three explicitly requested tasks in dependency order:
history restoration, one project schema upgrade, then storage analysis and explicit
cleanup. D024/D025's implemented persistence/freshness paths are used; their
remaining external acceptance gates are not claimed by this offline work.

The concrete D042 boundary is project schema 1 → 2: persistent artifact pins and
a resumable cleanup journal, needed by D047. Existing revisions, IDs, catalogs and
provider behavior remain unchanged. This is not a generic migration framework.

Restoration changes selections and preserves immutable revisions. Unrecorded
historical timestamps/provenance are displayed as unknown rather than fabricated.
Old media may remain stale for current inputs; restoration does not bypass freshness.

## D033: retained choices

Use **Project history** beside the project header. The browser shows retained
script snapshots and section revisions, visual prompt revisions, image variants,
generated raw/processed audio, timeline snapshots, and generated render results
where the existing model stores them. Text previews, creation times where recorded,
manual/generated/imported source, provider/model, image dimensions, audio duration,
selection indicators and editorial/input freshness are shown. Script/section
timestamps and provenance were not retained by v1; they show **Not recorded**.

Restoration compares the displayed script/selection token before writing. Section
restoration creates a new script selection; whole-script restoration selects a
retained snapshot. Prompt/image restoration appends an immutable selection event.
Timeline restoration appends a new event holding the previous arrangement. Audio
and render restoration atomically change existing result heads and rotate the
generation token, so a previously running generation cannot overwrite the restore.
All newer revisions/results remain available. No provider is invoked.

Freshness remains derived from consumed immutable inputs and current selections.
For example, B2 audio is unusable after restoring section B1; explicitly restoring
B1 audio makes that retained audio usable again. Unrelated A audio is unchanged.
An older image from a historical scene requires restoring its section/scene inputs
first; the existing selection validator refuses incompatible restoration. Old
timelines remain inspectable but need refreshed media bindings before rendering
when image/audio choices changed. Restoration never changes current provider
settings or claims that historical settings are current.

## D042: schema 1 → 2 and recovery

Opening a supported v1 project first locks owned SQLite writers and creates a
complete backup under `<project>/.backups/schema-1-…/`. `project.sqlite`, an existing
`jobs.sqlite`, and the existing artifact index are copied with SQLite's backup API,
including committed WAL state. Owned media/other project files are copied and
checksummed. Backup files have flat hashed names to avoid extending media path
depth on Windows; `backup.json` records their original relative paths/checksums.
Existing backups are excluded from new backups.

Only after backup completion does the transactional upgrade add `artifact_pins`
and `cleanup_items` and set `user_version = 2`. Existing IDs, revisions, selection
events, job records and artifact paths remain unchanged. Unknown/newer formats
are refused before upgrade writes or backup creation. A backup failure leaves the
original version untouched; failures before migration commit roll back the schema.
Abrupt process exit before commit also recovers through SQLite's rollback journal.
WAL journal normalization can be refused while another SQLite connection is open;
close that connection and retry. The complete backup remains available.

To restore, close the application and use a **new, nonexistent folder**:

```python
from app.storage.project_migration import restore_backup

restore_backup(
    r"D:\Projects\My film\.backups\schema-1-EXAMPLE",
    r"D:\Projects\My film recovered",
)
```

The service checks every backup file checksum and destination path before copying.
It never overwrites an existing project. The restored folder is the original v1
snapshot; opening it with the current application runs the same safe v1→v2 upgrade.
An older application will refuse the upgraded v2 project; there is no silent
downgrade. Backups need sufficient local disk space and are not cloud backups.
An `incomplete-…` folder is not a complete restorable backup and is never used
automatically. Storage cleanup protects backups, including incomplete ones.

## D047: explicit storage analysis and cleanup

Use **Project storage** for actual file sizes grouped as active media, history,
pinned/protected, active/interrupted jobs, regenerable cache, temporary workspace,
unused files and other protected project records. Analysis does not modify queue
state or delete files. Choose **Refresh dry run** to inspect the candidate paths
and reclaimable bytes, then **Delete previewed files** for the explicit action.
Pins persist in the project; removing an explicit pin does not remove protection
from history, selections or job references.

The reference graph follows artifact/value IDs and storage keys through retained
metadata/JSON snapshots and job requests, then protects their transitive inputs.
Retained media and unknown registered artifacts are kept by default. Registered
artifacts are collectable only when explicitly marked `disposable: true`, genuinely
unreferenced, unselected, unpinned and outside retained result history. The current
media intake/publication paths do not mark retained image/audio variants disposable.
Known revision records, image variants and section audio stay protected even if a
disposable hint was accidentally attached; hints cannot override retained history.
Unregistered files beneath the owned `artifacts/` tree can also be collected.
Both use a default seven-day grace period, never age alone.

Only the known project `cache/previews/` namespace is regenerable cache. It is kept
while queued/running/interrupted jobs could use it. Completed job workspaces are
identified by job IDs or their existing hashed workspace IDs. Unowned/ambiguous
workspaces are protected while resumable jobs exist; known project render, preview
and tempo workspaces are eligible after grace when unused. Shared audio/provider
caches outside the project, model folders, runtime installations, database files,
artifact staging and backups are protected, never cleanup candidates. Links or
junctions cause refusal rather than traversal/deletion.

Cleanup revalidates the preview and persists intent before moving eligible files
to an owned quarantine. Catalog removal and the quarantine journal transition
commit together using attached DELETE-journal databases. Before that commit the
original bytes can be restored; a new pin/reference causes resume to keep them.
After commit, quarantine disposal can resume safely. **Resume interrupted cleanup**
is explicit; opening a project does not silently finish destructive work. Reports
after cleanup count actual remaining files, including records/quarantine overhead.
Empty directories are harmless and are not recursively deleted.
Root backup/quarantine directories are also ignored by Git; deterministic fixtures
under `backend/tests/fixtures/` are not covered by these anchored ignore entries.

Meaningful retained history can still grow; this task does not promise bounded
history or automatically prune it. Cleanup intentionally leaves unknown files and
ambiguous workspaces for explicit diagnosis. No model/provider/runtime behavior
changes, shared-cache collector or application update mechanism is included.

## Validation evidence

The focused durability plus repository/editor/timeline regression suite passed
**72 tests** on Windows with isolated Python 3.11 and offscreen Qt. New tests cover:

- section B1 restoration with B2 retained, restart and stale-selection conflicts;
- prompt/image selection chains, unchanged source bytes and zero generation calls;
- audio restoration with B2 freshness invalidated, B1 reuse and unaffected A audio;
- retained timeline/render selection, restart and zero renderer calls during restore;
- version-1 fixtures, real image selections/IDs, complete backup restoration, unknown
  newer-schema byte equality, committed WAL state and migration failure injection;
- abrupt migration process exit before commit and rollback recovery;
- pinned, selected, historical and interrupted-job references, grace, dry-run byte
  equality, actual cleanup/accounting and second-cleanup idempotency;
- cleanup interruption after intent, file quarantine, catalog commit and item commit,
  restart/explicit resume, plus pinning during interrupted quarantine recovery;
- Qt history restoration and storage deletion only after explicit button action.

After reconciling the pre-existing project-version assertions with the intentional
schema upgrade, the affected queue/plan/publication/migration/storage run passed
**87 tests**. The latest storage/migration run passed **21 tests**, including
corrupt-backup refusal and mandatory retention despite a disposable hint. These
focused runs overlap; their counts are not summed.

Final `python -m pytest backend/tests`: **1972 passed, 11 existing optional tests
skipped in 564.65 seconds**. `git diff --check`: **PASS**. The 29 new regression
cases cover these three tasks; D033, D042 and D047 are **Completed** against their
specified repository acceptance criteria.
The tests do not claim clean-machine installer, human audiovisual, paid-provider
or GPU acceptance. Those existing external gates remain with their owning tasks.

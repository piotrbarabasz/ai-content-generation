# Shorts non-blocking media preparation (SHORTS-003)

Date: 2026-10-20. Branch: `perf/shorts-nonblocking-media`.

The starting worktree was `70a104b` with the preceding playback repair still
uncommitted. Those changes are preserved. This is the user-selected D073
performance boundary in `docs/desktop/IMPLEMENTATION_PLAN.md`; no merge or paid
provider benchmark was performed, and no production project was exported.

## What profiling found

The initial measurements on the playback-repair worktree found owner-thread
stalls of 1,390 ms opening Shorts, 319 ms switching retained cards, 429 ms copying
and hashing a 256 MiB source, 408 ms looking through three retained packages,
and 919 ms saving a copy. These are actual local measurements, not estimates.
Raw results: [initial profile](shorts-nonblocking-media-evidence/before-playback-worktree.json).

The Qt timer pumps the application's asyncio loop on the GUI thread. Declaring
`preview`/`render` async therefore did not move their synchronous preparation,
hashing, history reads, ZIP work or publication transfers off that thread.
`ProjectShorts.cached` and the Director equivalent also verified unrelated
outputs before testing their fingerprints. Card restoration repeatedly invoked
these checks, and renderer identity repeatedly hashed the media executables.

## Changes and integrity boundaries

- Detached, operation-scoped readers extend the existing frozen render-reader
  pattern. The owner captures manifests, script and heads. Workers verify files,
  source lineage, edits, narration, images, renderer identity and complete output
  packages. Repeated source/renderer reads share receipts within that operation.
  Later actions capture and hash fresh bytes. There is no persistent stat-only
  checksum cache.
- `MediaSnapshotStore` contains a file-only catalog, cancellable reads, bounded
  small-payload reuse and file-change signatures. Deferred caption/image/edit
  publications retain the existing recovery journals. The owner checks its live
  identity and file signatures before registering their manifests. Live project
  and derived-project SQLite connections never enter a worker. The channel
  library adapter already opens/closes a connection per operation; its lazy
  composition factory is resolved on the owner rather than passed to workers.
- Large MP4/package publication transfers now use the existing immutable-store
  protocol in two phases: copy/hash/journal/final-file visibility in a worker,
  catalog registration on the owner. Exact decoded checksum and size are checked
  before the transfer is committed. Recovery and append-only selection semantics
  are unchanged. A failed or canceled attempt cannot replace the prior selection.
- Original Excerpt and Director recheck current source/revision/approval identity
  after encoding and before selecting the newly published output. Source staging
  is also hashed after encoding. Failures retain previous outputs and diagnostic
  work files. Duplicate renders are rejected; the desktop prevents overlapping
  render/export actions and the exporter rejects duplicate destination writes.
- Card lists show retained-output presence without opening every MP4/ZIP. Opening
  a card verifies only its selected output. Unknown, stale or damaged output
  cannot become the ready playback/export selection. Cards remain editable when
  retained output verification fails. Histories use scoped queries and owned ID
  lookups; cache lookup filters fingerprints before expensive media verification.
- ZIP SHA-256 and every member's CRC remain checked. Reading members through EOF
  replaces `testzip()` with equivalent CRC checking in cancellable, bounded
  chunks. Packaging streams to disk instead of holding another full MP4 ZIP in
  `BytesIO` on the GUI thread.
- Director PCM/image preparation and vertical-preview preparation run in workers.
  FFmpeg remains the existing asynchronous subprocess implementation, with its
  decode, packet, frame, duration, codec and A/V checks. Its output hashing also
  runs in cancellable workers; rendering filters and artifact formats are retained.
- Shorts/Director exports choose the destination on Qt, write and independently
  read back a private destination copy in a worker, revalidate the current source
  and revision on the owner, then atomically replace the destination. Failure or
  cancellation removes only that private copy and preserves an existing export.
  Other desktop consumers retain the existing synchronous export API.
- UI phases include Checking source, Preparing excerpt, Generating vertical
  preview, Encoding, Verifying, Saving and Ready/Failed/Canceled. Missing media,
  access failures and disk-full errors include recovery guidance. Provider work
  is never automatically retried.

## Source staging reuse

Each source checksum gets a verified private staging cache under configured
`work/shorts-sources/`. The first preparation copies and hashes the original;
subsequent attempts rehash both the original and the cached source, then hard-link
the cache into a unique attempt directory. The original artifact itself is never
hard-linked into writable work. Concurrent first preparations cannot overwrite
the cache. Incomplete attempts are not reusable. Corrupt cache bytes are rejected
and retained. Filesystems without hard links use the checked-copy fallback.

This removes repeated full-documentary **writes and disk allocation** on NTFS,
not the necessary verification reads. It can take longer than a warm copy, as the
measurement below shows. Bounded extraction was not adopted: the existing renderer
validates the original complete frame count, dimensions, duration and timing
evidence. Changing those semantics would require a separate extraction contract.

## Repeatable before/after measurements

Environment: Windows, Python 3.11.9 in `.venv`, PySide6/Qt 6.11.2, FFmpeg 8.1.2.
`reports/shorts_media_benchmark.py` creates all inputs in a disposable temporary
directory. The 256 MiB payload is an immutable I/O surrogate, not a playable MP4.
Three tiny retained outputs use the offline mock renderer. A separate real
90-second 160×90 H.264/AAC clip measures vertical still generation. No provider or
user project is involved. A 2 ms Qt heartbeat pumps the same async operations.

The initial profile above used the actual playback-repair worktree before edits.
For a reproducible repeat, the same benchmark also ran against detached audit
commit `70a104b` in `.tmp/shorts-media-baseline`, whose media I/O implementations
predate this change. Its playback presentation differs; its synchronous copy,
verification and export paths are the relevant baseline. Both raw comparisons
are retained. Numbers below are single local runs with warm filesystem effects;
they are not production throughput guarantees.

| Operation | Before elapsed | After elapsed | Before max heartbeat gap | After max gap |
|---|---:|---:|---:|---:|
| Open/restore Shorts | 1,435 ms | 217 ms | 1,433 ms | 22 ms |
| Switch retained card | 324 ms | 106 ms | 322 ms | 20 ms |
| First 256 MiB staging | 402 ms | 410 ms | 401 ms | 18 ms |
| Repeated staging | full copy each time | 527 ms, 0 bytes copied | same blocking copy path | 20 ms |
| Find retained output/package | 359 ms | 49 ms | 357 ms | 17 ms |
| 256 MiB export | 577 ms | 805 ms | 576 ms | 18 ms |
| Produce real vertical still (service) | 241 ms | 309 ms | 27 ms | 7 ms |

The first observable Qt heartbeat after Open Shorts dispatch fell from 1,435 ms
to 4 ms; after card switching it fell from 324 ms to 6 ms. This measures when the
owner can service the status/event loop, not a screenshot-based paint latency.
Dispatch itself was already fast; the subsequent owner-thread work caused stalls.
The still measurement ends when verified PNG bytes return, before Qt pixmap
presentation. Export now includes an independent destination readback and staging
reuse verifies two existing files, explaining the elapsed-time tradeoffs.

A cancellation scheduled at 20 ms was not serviced during the baseline staging
copy (413 ms completion, cancellation not honored). With the worker it was honored
and returned at 46 ms after dispatch, approximately 26 ms after its deadline.
The deterministic tests additionally stop a copy after its first MiB and verify
that the partial attempt is retained and the previous selection is unchanged.

The retained-output lookup verified **one** matching MP4/package instead of
**three**. Direct ID lookup calls increased from 3 to 9 because they replace broad
history discovery and enforce ownership for each dependency; these calls use the
detached in-memory catalog. The source-reader test also proves that repeated
source/timing reads hash the original once per operation and a subsequent
operation detects same-size source corruption.

Raw repeat data: [before](shorts-nonblocking-media-evidence/before-audit-commit.json),
[after](shorts-nonblocking-media-evidence/after.json). Reproduce from the repository:

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe reports/shorts_media_benchmark.py .tmp/shorts-media.json
```

## Remaining owner-thread work and limits

- SQLite snapshot capture, selected-head/revision comparison, child scope
  open/close and final catalog commits must stay on their owning thread. Capture
  includes project manifests and retained section identities; it is not a new
  constant-time indexed snapshot protocol. Very large catalogs remain a scaling
  limit. Legacy sidecar-only compositions also scan their catalog at capture.
- Small JSON/SRT metadata publications retain their existing synchronous
  journal/fsync/catalog operations. Long media streams and ZIPs use workers.
- File-signature checks, contained-path resolution and the final atomic export
  rename run on the owner. They read metadata, not media payloads. A pathological
  filesystem/antivirus can still delay an individual metadata operation.
- File dialogs, widget/card construction, `QPixmap` presentation/scaling and
  `QMediaPlayer` source changes remain on Qt. Qt's internal decoder behavior is
  outside the file-worker boundary. No change is made to the prior playback fix.
- Cancellation is cooperative between I/O chunks and bounded image operations;
  an in-flight OS write/fsync or Pillow call must return before cleanup finishes.
  Async task cancellation drains the worker, so it cannot publish later or touch
  a closed project after the UI reports completion.
- Other branding/delivery panels still use the original synchronous export helper;
  this selected task changes the two Shorts workflows only.

## Validation

Focused coverage includes real indexed parent/derived SQLite ownership, fresh
source hashing, source mutation during preparation, verified hard-link reuse and
checked-copy fallback, cancellation after staging begins, cancellation during the
render process boundary, duplicate renders, corrupt package selection, disk/write
failure, source/revision changes during export, atomic destination preservation,
project-switch guards, draft restoration and real Qt decoding/seek/error recovery.
All fixtures are disposable and offline.

- 47 guard + Excerpt UI tests passed in 89.99 seconds before the final additional
  corrupt-package and native playback verification run.
- The final focused UI/native run passed 55 tests.
- The final guard/Excerpt UI run passed 48 tests in 91.39 seconds, including the
  additional corrupt-package selection case and cancellation-safe source refresh.
- The initial broader service/media/storage run had 74 passes and one cancellation
  test synchronization failure; the test now waits for worker token propagation
  before releasing its blocked read. The corrected guard test passes.
- The follow-up geometry hardening carries these changes on
  `fix/shorts-geometry-validation`. Its broader application/media regressions passed
  121 tests; see [geometry hardening](SHORTS_GEOMETRY_HARDENING.md).
- Final combined `python -m pytest backend/tests --tb=short`: **2,674 passed,
  12 skipped, 1 failed in 1,675.16 seconds (27m55s)**. All **184 Shorts/Director
  cases passed**. The sole failure is the known baseline
  `test_d078_cloud_measurements_drive_duration_repair_without_images`, with
  `cloud_audio.py:406` reporting incomplete section synthesis and retained valid
  retry chunks. Both files are unchanged; the audit and playback verification
  reproduced the same failure. No additional failures were introduced.
  [Machine-readable test summary](shorts-nonblocking-media-evidence/test-summary.json).
- `git diff --check`: clean.

The earlier full run was restarted when the user added the geometry task; the
final command above verifies the combined worktree. Changes remain uncommitted
and unmerged.

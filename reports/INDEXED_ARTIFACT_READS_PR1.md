# Performance PR 1: indexed artifact reads and scoped history (D081)

Date: 2026-10-17. Base: `fix/final-render-readiness` at
`fb0c1a646d988646ca2877b8731ff282282f0fb4`. Implementation branch:
`perf/indexed-artifact-reads`. No merge or remote publication is part of this work.

## Repository inspection and boundary

The base had no indexed artifact-read implementation. `ProjectArtifactIndex`
exposed only `manifests()`, while artifact-ID and storage-key opens enumerated
every manifest. `ProjectScenePlans`, image reads/selections, section audio and
narrative record reads added nested catalog enumeration. The only initial
uncommitted file was the supplied `VIDEO_PREVIEW_PERFORMANCE_AUDIT_2026-10.md`;
it was left unchanged and excluded from this commit.

The user selected this focused performance task. D081 records its boundary in
the desktop backlog separately from D080's retained-render behavior fix.

## Changes and preserved behavior

- ID/key queries use the existing primary-key/unique indexes. `EXPLAIN QUERY
  PLAN` regression tests assert indexed searches. The returned manifest must
  agree with its index row's ID, key, checksum and size.
- Point queries still open a guarded connection: project ownership, live-session
  access, index format and contained database paths remain checked. File opens
  still require registration and resolve through contained configured storage.
- Scene, acceptance, timing, image, selection, timeline and narration histories
  filter available metadata before constructing Python manifests or loading
  unrelated payloads. Duplicate value IDs remain ambiguous. Selection-chain,
  ownership, checksum and dependency validation remain in place.
- New narration records carry optional scope/section/revision headers. Historical
  records without those headers remain candidates and are filtered only after
  verified payload loading. Old JSON sidecar stores keep their existing fallback.
- One `ProjectTimelineMedia.resolve` or `boundaries` invocation uses private
  copies to reuse verified scene records and image lineage. Nothing is retained
  on the live adapter. Every scene resolution in the compiler's second pass,
  every readiness invocation and both preview currentness gates start fresh.
- Frozen render inputs use local ID/key maps; the read-only narration recovery
  bridge implements the same read interface without acquiring writable sessions.
  No global cache, schema migration, provider dependency or new framework exists.

Audio PCM measurements/checksums, source-image lineage/checksums, immutable scene
record checksums, trim boundaries, exact selected audio variants and proxy hashes
are still verified. Timeline selection/currentness rules and the compiler's
second pass are unchanged. Historical rejected or superseded artifacts remain.

## Benchmark method

`benchmark_indexed_reads.py` reopens only the original audit's retained synthetic
fixtures: `cold12sec_v2/project` (two distinct six-second scenes, motion-master
lineage, 17 manifests, retained 12-second proxy) and `scaling64/project` (64
distinct 0.2-second imported-image scenes, one section WAV, 261 manifests).
16/64 readiness uses prefixes of the same saved 64-scene timeline. No fixture
rebuild, staging, provider call or encode occurs in this harness.

Both versions use the same Python 3.11 virtual environment, instrumentation,
fixtures and scenario order. SQLite tracing is installed at connection creation,
including owner/format queries; Python manifest construction and calls to the
full `ProjectArtifactIndex.manifests()` API are counted. JSON metadata filtering
still scans rows inside SQLite: a reported zero means zero complete catalog
enumerations/reconstructions in Python, **not** zero SQLite table scans.

Exact ID/key timings cover the same public artifact-open entrypoints, including
lookup, registration, path containment and file open/close, without reading the
media bytes, at both 17 and 261 catalog manifests. Twenty consecutive opens are averaged. Heartbeat gaps for these
rows cover the entire batch, not one lookup. Other scenarios are single samples.
The cached-proxy row invokes the actual `PreviewService.proxy`, both live gates,
saved timeline history and real proxy checksum lookup. Its renderer identity is
the retained synthetic cache identity; any cache miss fails without encoding.
This is service-return latency, **not** click-to-first-frame or player latency.

A real Qt event loop runs a 5 ms heartbeat around each synchronous owner-thread
operation. Maximum dispatch gaps measure starvation on this path. These are
descriptive measurements on the same host, not throughput percentiles, production
estimates or claims that OS filesystem/antivirus state is controlled. The audit's
original 77.445 s sample is not used as the before value for a different run.

Reproduction (PowerShell, with retained audit fixtures available):

```powershell
# Baseline source checkout (created separately with git worktree add --detach):
.venv/Scripts/python.exe reports/benchmark_indexed_reads.py $env:TEMP/aics-preview-audit-20261017 $env:TEMP/pr1-before.json --source-root $env:TEMP/aics-pr1-baseline
.venv/Scripts/python.exe reports/benchmark_indexed_reads.py $env:TEMP/aics-preview-audit-20261017 $env:TEMP/pr1-after.json
```

## Results

Final back-to-back measurements, with pytest finished. Python 3.11.9 / PySide6 6.11.2. The saved timeline IDs, catalog-header hashes, catalog sizes and proxy identity/checksum match across both runs; [raw paired results](INDEXED_ARTIFACT_READS_PR1_BENCHMARK.json) retain those fingerprints.

| Operation | Before | After | Qt maximum gap, before → after |
| --- | ---: | ---: | ---: |
| ID open, 17 manifests | 3.752 ms | 5.203 ms | 77.195 ms → 104.304 ms |
| Key open, 17 manifests | 3.908 ms | 6.128 ms | 78.317 ms → 122.713 ms |
| Two-clip currentness | 1.040 s | 0.571 s | 1.045 s → 0.577 s |
| Two-clip cached proxy service | 2.070 s | 1.109 s | 2.071 s → 1.109 s |
| ID open, 261 manifests | 7.145 ms | 6.751 ms | 147.965 ms → 136.192 ms |
| Key open, 261 manifests | 7.329 ms | 5.538 ms | 146.713 ms → 116.245 ms |
| 16-clip readiness | 11.847 s | 3.658 s | 11.848 s → 3.663 s |
| 64-clip readiness | 57.537 s | 19.938 s | 57.537 s → 19.939 s |

Counts below cover one operation, except point opens, which cover all 20 opens in each row. Arrows are before → after.

| Operation | Catalog enumerations | Manifest reconstructions | SQL statements |
| --- | ---: | ---: | ---: |
| ID open, 17 manifests | 20 → 0 | 340 → 20 | 120 → 120 |
| Key open, 17 manifests | 20 → 0 | 340 → 20 | 120 → 120 |
| Two-clip currentness | 232 → 0 | 3,944 → 96 | 1,512 → 636 |
| Two-clip cached proxy service | 468 → 0 | 7,956 → 196 | 3,048 → 1,296 |
| ID open, 261 manifests | 20 → 0 | 5,220 → 20 | 120 → 120 |
| Key open, 261 manifests | 20 → 0 | 5,220 → 20 | 120 → 120 |
| 16-clip readiness | 1,248 → 0 | 325,728 → 640 | 8,352 → 4,480 |
| 64-clip readiness | 4,992 → 0 | 1,302,912 → 2,560 | 33,408 → 17,920 |

Point-read wall times do **not** show a universal speedup: the 17-manifest samples were slower after the change, and the 261-manifest ID sample changed little. The reliable point-read improvement is bounded reconstruction (one manifest per open) and an indexed search. Existing connection/ownership/path checks remain, and host timing varies. Bulk currentness/readiness improved in these paired samples, while synchronous Qt dispatch still stalls for the duration of verification. No playback first-frame or production latency claim is made.

## Regression validation

New deterministic temporary-project regressions cover indexed plans, no catalog
enumeration for ID/key and compiled media reads, unknown IDs, unregistered files,
opaque/path-like IDs, traversal, closed sessions, changed ownership, corrupt index
identity, legacy sidecars, fresh byte checks between operations, scoped scene
history, missing legacy scope headers, relevant corruption, scoped narration and
nested audio metadata. Existing suites cover selection branches/history,
historical media, reopen/relocation, currentness, trim/variant behavior, preview,
render publication and offscreen Qt readiness.

The known `test_d078_cloud_measurements_drive_duration_repair_without_images`
failure was reproduced on untouched `fb0c1a6` in a detached temporary checkout:
`Section synthesis incomplete; valid chunks remain available for retry.` No
unrelated duration-repair change is included.

Focused runs passed:

- 106 Timeline, scene-plan, image-intake, video-render and publication cases.
- 81 Timeline/artifact-store/streaming unit cases.
- 48 section-tempo and narration-recovery cases.
- All 13 new indexed/scoped-read cases and four Export-panel cases.

Full final run: **2,565 passed, 12 skipped, one pre-existing failure** in
2,068.37 s. Command:

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -v --tb=short -o faulthandler_timeout=120
```

The sole failure is the baseline duration-repair case described above; no new
failing case remains. The full run includes all 13 new regressions.
`git diff --check` passed.

## Remaining bottlenecks and limits

- Each point query still opens SQLite and repeats its owner/format/path guards.
  Removing catalog reconstruction does not guarantee lower latency on a tiny
  catalog; filesystem and connection costs can dominate.
- Type/value/scope metadata predicates use existing JSON headers without new
  metadata indexes. SQLite row filtering remains linear in catalog size.
- Reuse is deliberately bounded to a single media resolution, preserving fresh
  checks between clips and compiler passes. Shared section audio and retained
  plans are still verified repeatedly across clips, followed by trim checks.
- Both preview gates and synchronous preview preparation remain. The owner-thread
  heartbeat therefore still stalls. D080's worker snapshot capture/live comparison
  still enumerates the catalog; this PR does not remove that safety boundary.
- The production renderer's motion-settings callback still enumerates the catalog
  in `ProjectMotionSettings.current`; the benchmark pins the retained identity
  identically on both versions and excludes that callback/binary identity hashing.
  Legacy narration's `migrated()` catalog check also remains outside this measured
  currentness path. Zero enumerations here is not a claim about every desktop action.
- Timeline and image selection histories still validate their complete relevant
  chains. A very long history for one scene/project can still be expensive.
- Staging copies/conversion, proxy identity hashing, encoding, output validation,
  desktop refresh amplification and actual Qt playback are unchanged. Production
  latency and a full production render were not measured.

No production project was opened or modified. No historical media/cache cleanup,
paid calls or production render was performed. Test media lives in temporary
synthetic projects; no media artifacts, secrets or caches are committed.

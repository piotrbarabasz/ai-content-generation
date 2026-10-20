# Video preview performance audit — October 2026

Audit date: 2026-10-17. Baseline: branch `fix/final-render-readiness`, commit `fb0c1a646d988646ca2877b8731ff282282f0fb4`. Working tree was clean before this audit. Local source is authoritative. Only this report is added to the repository; instrumentation and synthetic projects are under `C:\Users\user\AppData\Local\Temp\aics-preview-audit-20261017`.

## A. Executive summary

**Diagnosis: MULTIPLE BOTTLENECKS, led by slow source/currentness validation on the cached preview path, with additional synchronous staging and cold proxy encoding costs. Slow Qt playback is not demonstrated.**

The existing 627.8-second Soviet Moon proxy is intact. Its SHA-256, size, manifest, retained timeline identity and renderer identity match. Isolating cache verification and actual PreviewPanel playback produced a first valid Qt video frame in **0.670 s initially / 0.341 s on repeat**. This bypasses live source-currentness validation and is explicitly not the complete production button latency.

The complete application preview path on an isolated 12-second, two-clip project took **20.383 s cold / 7.998 s cached / 7.138 s repeat** to its first valid frame. On the cached run, **7.355 s** was spent in the two source-currentness gates, versus **0.018 s** in the actual cache lookup and **0.478 s** from the playback route to first frame. That hit still performed **468 full catalog scans, 7,956 manifest reconstructions and 3,048 SQLite statements**, despite a catalog of only 17 manifests. It did not encode or stage again.

The 64-clip synthetic timeline took **77.445 s for readiness and 35.793 s for staging**, before any encoding. It reconstructed **1,302,912 manifests in 4,992 readiness scans**. Short media does not eliminate readiness/staging latency. These are actual distinct retained scenes, not 64 aliases of one clip. The nested catalog/path work, source lineage checks and duplicate timeline resolution dominate the cached path. Do not attribute these times to hashing the proxy itself.

**Production limitation:** a read-only `mode=ro`, `timeout=0` query of `project.sqlite` failed with `database is locked`. The owning session was left untouched. Full production currentness, selected live motion settings and complete production click-to-first-frame are **NOT MEASURED**. No immutable-mode lock bypass, writable project opening, production staging, rendering, publication, recovery or cache invalidation was attempted. The retained timeline JSON and existing cache files were read directly. This establishes a valid retained cache, not a claim that the currently open editor's live selections passed their currentness gate.

Environment: repository `.venv`, Python 3.11.9, PySide6/Qt 6.11.2; PATH FFmpeg, FFprobe and FFplay 8.1.2 Gyan full shared build. Executable identities match the proxy manifest. Host: Ryzen 3 3100, 4 cores/8 logical processors; GeForce GTX 1660 SUPER. Source-development media discovery uses PATH; installed bundles instead use their own `media` directory. No packages or players were installed. Configuration secrets and project script contents were not logged.

## B. Actual call graph and thread ownership

Source locations refer to this baseline. All paths start under `backend/app/`.

| Step | Exact path | Thread / activity |
|---|---|---|
| Button | `desktop/preview_panel.py:195 PreviewPanel.preview_film` | GUI: `clear()` stops playback, empties source, hides video; creates asyncio loop/task; starts 15 ms Qt timer |
| Coroutine execution | `PreviewPanel._tick:209` pumps one asyncio iteration with `run_forever()` | **Same GUI thread**. Async syntax does not move synchronous calls to a worker |
| Initial currentness | `application/preview.py:67 proxy` → `_is_current:44` → `current_edit()` → `ProjectTimelineEdits.current/history` → `ProjectPreviewMedia.current` → `ProjectVideoRender.current:202` | GUI: retained history reads, checksums, catalog access, SQLite, media validation |
| Compile/resolve | `application/timeline.py:32 TimelineCompiler.compile` → `ProjectTimelineMedia.resolve:39` twice per clip; then `ProjectVideoRender.current` → `boundaries` once per clip | GUI: plans/acceptance/timing, selected audio, image selection chain, recursive source image lineage, complete WAV/image reads and hashes; compares retained snapshot and allowable boundaries |
| Renderer/key | `FFmpegRenderer.identity:57` → two executable hashes + configured motion; `proxy_cache_key` fingerprints exact timeline payload + renderer identity | GUI: hashing, JSON/fingerprints; production composition's motion callback scans retained motion preference history |
| Cache lookup | `storage/preview.py:56 cached_proxy` → `_manifest`, contained path, size, full MP4 SHA-256 | GUI: filesystem and hash; no ffprobe or full video decode on a hit |
| HIT gate | `PreviewService.proxy` returns after **another `_is_current(timeline)`** | GUI: repeats entire source resolution. Cache success does not bypass it |
| MISS preparation | `ProjectPreviewMedia.stage` → `ProjectVideoRender.stage:217` → `_stage_clip:258` | GUI: creates private work directory, resolves retained inputs again, copies full section WAV per clip and image source, hashes copies, Pillow EXIF/RGBA/black composition/RGB/PNG |
| Command/filter setup | `providers/ffmpeg_render.py:70 render` | GUI: per-clip input/filter construction; writes `filters.txt`; production renderer freezes motion configuration once |
| Encoding | `runtime/media_process.py MediaProcess.run` → native FFmpeg | Separate native process. Async pipes/cancel watchers run through the GUI-owned asyncio loop; Windows job owns child cleanup |
| Validation | `FFmpegRenderer.validate:160` | GUI hashes output; native FFprobe `-count_frames`; native FFmpeg full decode with `-xerror -err_detect explode`; GUI hashes output again |
| Cache publication | `ProjectPreviewMedia.publish_proxy:68` | GUI hashes validated source again, copies MP4, flush/fsync, writes/fsyncs manifest, atomically replaces cache paths; native encode already finished |
| Final MISS gate | `not canceled()` + `_is_current()` + fresh `renderer.identity()` | GUI: source checks again; changed results retained but not played |
| Presentation | `_tick` → image hide/video show → `_play:125` → `_prepare_audio` → `player.setSource(local URL)` → `player.play()` | GUI calls; Qt backend loads/demuxes/decodes asynchronously; video sink delivers first valid frame to GUI signal handler |

The player and QAudioOutput are constructed once per PreviewPanel. Film clicks do not recreate them. However every film click calls `clear:244` → `stop()` → `setSource(empty)` and later reasserts device, mute, volume and audio-output attachment. Stop alone only invokes `player.stop()` and does not validate media. There is no simple retained-source play button in this panel: clicking Render/play again repeats preparation checks and resets the source.

`desktop/preview_composition.py compose_preview` constructs only preview/render/storage services, not an AI generation provider. Final export is a separate path: `desktop/export_panel.py play` verifies the selected final render and calls `QDesktopServices.openUrl`; it does not use PreviewService or create a film proxy. It was inspected, not invoked on the protected unpublished final video.

## C. Measured phase timings

All times use `time.perf_counter`. Single-run descriptive samples, not percentiles or controlled throughput claims. Warm filesystem/antivirus state and other desktop processes were not controlled. Some synthetic fixture preparation overlapped other harnesses. Counts and execution paths are stronger evidence than small timing differences. Synthetic pixels are flat color and audio is deterministic PCM: these timings must not be multiplied into a production encoding ETA.

### Complete 12-second synthetic flow (scenario C, plus cached repeats)

Two distinct six-second scenes, 2400×1350 retained motion masters derived from synthetic 640×360 sources, one 12-second WAV, saved timeline, actual PreviewPanel, real FFmpeg and Qt. No paid provider. Initial miss was a new empty synthetic cache directory; the subsequent two operations reused the generated file.

| Phase | Cold proxy | Cached proxy | Repeat playback |
|---|---|---|---|
| Timeline/currentness validation (both gates) | 5.644 s | 7.355 s | 6.710 s |
| Renderer identity (all calls) | 0.004 s | 0.001 s | 0.004 s |
| Cache lookup including MP4 hash on hit | 0.004 s | 0.018 s | 0.003 s |
| Staging | 2.158 s | NOT APPLICABLE | NOT APPLICABLE |
| FFmpeg encode | 10.214 s | NOT APPLICABLE | NOT APPLICABLE |
| Verification: probe + full decode | 1.572 s | NOT APPLICABLE | NOT APPLICABLE |
| Publication | 0.026 s | NOT APPLICABLE | NOT APPLICABLE |
| Qt setSource route to first valid frame | 0.664 s | 0.478 s | 0.299 s |
| Total click to first valid frame | 20.383 s | 7.998 s | 7.138 s |

Verification timings above are native probe/decode waits; output hash time is included in total but not separately timed there. Publication includes its own source checksum/copy/fsync. Small unassigned time includes cache-key construction, Qt timer scheduling and source-reset/UI work; columns are not asserted to sum exactly. Cold click-to-path handed to `_play` was **19.719 s**; initial validation plus staging was **5.202 s**, with staging alone **2.158 s**. Native waits were encode **10.214 s**, probe **0.943 s**, full decode **0.629 s**. First run independently observed encode **7.141 s**, staging **0.905 s**, illustrating host/run variability.

Cold total: 576 catalog scans / 9,792 reconstructed manifests / 3,712 SQL statements. Each cached run: 468 / 7,956 / 3,048. SQL trace in the corrected harness starts at connection creation and includes owner/format/path guard queries. No SQL text or project script was retained. CPU time in the corrected harness was 5.625 s cold, 3.875 s cached and 3.547 s repeat (process CPU, not normalized CPU utilization, and excludes native child CPU). Native wall waits are measured separately.

### Existing production proxy in isolation (scenarios A/B)

Exact path:

`D:\Projects\ai-content-generation\projects\What-If-the-Soviet-Union-Landed-on-the-Moon-First\cache\previews\34e50fa969765af1514d0a7ee273a937178cd7067156a40a84114e5faaa7bde2.mp4`

This uses actual PreviewPanel click/reset/playback and actual `ProjectPreviewMedia.cached_proxy`, with a harness service returning the verified retained cache. **Both live `_is_current` gates are deliberately outside this measurement because the production database was locked. No production service behavior was changed.** Identity/key were checked separately before playback. Thus 0.670/0.341 s are isolated playback results, not full real-project cache-path totals.

| Run | `_play`/setSource route begins | Loading | Loaded | Playing | Buffered | First valid frame |
|---|---|---|---|---|---|---|
| A_direct_verified_cache | 0.244 s | 0.246 s | 0.464 s | 0.566 s | 0.632 s | 0.670 s |
| B_repeat_verified_cache | 0.205 s | 0.205 s | 0.213 s | 0.259 s | 0.310 s | 0.341 s |

Cache lookup, including full MP4 hash: **195.293 / 85.818 ms**. Renderer identity: **2.019 ms**; exact 64-clip cache key: **30.529 ms**, separately measured. `_play` route to frame: **426.345 / 135.737 ms**. The timestamp precedes `_prepare_audio`, so includes audio routing/show-hide work rather than pretending to isolate just setSource's function call. Loading signal was 2.213 / 0.217 ms later. No Error, InvalidMedia or StalledMedia was observed. Playing preceded the first frame by **104.025 / 81.810 ms**; Playing is not a first-frame measurement.

The frame endpoint is the first **valid `QVideoWidget.videoSink().videoFrameChanged`** while the widget is shown. It demonstrates decoded frame delivery, not a photometrically measured screen/compositor presentation. Physical display latency is NOT MEASURED.

Separate three-sample read-only MP4 verification: JSON manifest **0.248–1.984 ms**, stat **0.046–0.128 ms**, SHA-256 **84.551–189.822 ms**; all sizes and checksums matched. No extra decode or hashing is hidden in cached_proxy beyond that single full MP4 hash.

### 64 distinct short scenes, no encode (scenario D)

One synthetic section containing 64 paragraph scenes, each 0.2 s; total 12.8 s. Retained imported 1920×1080 images, one complete 12.8 s WAV, 261 catalog manifests after timeline save. Subsets contain 1/16/64 distinct scenes. Readiness and staging timed independently; compiling each fixture/subset beforehand is excluded from these phase timings. No 64-clip encode was run.

| Clips | Timeline duration | `current` readiness | Readiness scans | Manifests parsed | `stage` | Stage scans | Staged files / bytes |
|---|---|---|---|---|---|---|---|
| 1 | 0.2 s | 1.074 s | 78 | 20,358 | 0.560 s | 35 | 3 / 222,026 |
| 16 | 3.2 s | 19.703 s | 1248 | 325,728 | 11.935 s | 560 | 48 / 3,552,416 |
| 64 | 12.8 s | 77.445 s | 4992 | 1,302,912 | 35.793 s | 2240 | 192 / 14,209,664 |

Each clip stages three files: full WAV, `.source` image and normalized PNG. Every scene shares the same section WAV yet it is copied once per clip. SQL counts in the initial scaling harness omit statements executed before its late connection trace callback; they are not presented as full query totals. Scan/manifest counts include the complete method calls and are reliable. This fixture has simpler imported-image lineage than the production motion-master project; its latency is not a production latency estimate.

### Detailed I/O/profile and existing worker boundary

Separate instrumented two-clip currentness read **1,369,544 bytes through 120 Python binary file opens**. Stage read **1,305,442 bytes through 62 binary opens**, excluding SQLite/native/OS/implicit Pillow file opens. It wrote six staged files: two 192,044-byte full WAVs and four 12,569-byte image source/PNG files, **434,364 staged bytes**. These are application read counts, not physical disk bytes (OS cache can serve them).

In a separate cProfile currentness sample: total **4.413 s**, `TimelineCompiler.compile` **4.037 s**, four `resolve` calls **4.036 s**, 232 catalog `manifests` calls **3.342 s**, `contained_path` **2.751 s**, 2,128 Path.resolve calls **1.718 s**, 5,672 Windows `_getfinalpathname` calls **0.995 s**. Values are nested and must not be added. Much of the cost on this small catalog is repeated ownership/containment/path/connection work, not just JSON parsing.

Separate profiled stage **2.597 s**: catalog reads **1.595 s** cumulative; `contained_path` **1.367 s**; PNG saving **0.119 s** and alpha composition **0.111 s**. Source copying and staged checksum substeps were executed and included; their exclusive wall costs were NOT separately isolated. Full-resolution Pillow preparation is measurable, but is not the principal cost in this small sample.

Existing `ProjectVideoRender.current_async` and `stage_async` were exercised on this isolated two-clip project: **0.801 s / 1.237 s** elapsed, with maximum Qt heartbeat gaps **38.0 ms / 161.2 ms**. `_stage_clip` ran on thread 6860, owner thread 33352. These methods capture immutable project/script/manifest/head data on the owner and operate without SQLite in workers. `current_async` then rechecks live owner state; `stage_async` propagates cancellation and drains the worker while retaining staged bytes. This is a demonstrated reusable boundary, not an implemented preview fix or a controlled speedup ratio. Snapshot capture/owner rechecks can still stall and require profiling at production scale.

## D. Cache diagnosis

**Existing proxy: HIT against the retained saved timeline and recorded motion configuration.** Key `34e50fa969765af1514d0a7ee273a937178cd7067156a40a84114e5faaa7bde2`; timeline `timeline_fd02a8a83e5916595f66e77f7b9ecc3b5206e9de6a47ff672727cd5ed5274394`; size **77,317,392 bytes**; SHA-256 `82ee5b2a6c3bd6b7416b179e16f391d74cdc1c27d097c68874435fa0fd3fd215`. Manifest is version 1 / kind film_proxy and renderer fields match local binaries. Motion: `motion-controls-v2`, zoom subtle, pan off, adapter `proxy-motion-mp4-v2`.

| Scenario | Result | Exact reason |
|---|---|---|
| Existing retained production cache, twice | HIT | Same timeline ID/payload-derived key, renderer binary hashes/motion identity, manifest version/kind, file size/checksum |
| Fresh isolated 12 s fixture | MISS | Neither key-specific cache manifest nor MP4 existed in the new synthetic directory; no identity mismatch |
| Same synthetic fixture after render; next click and repeat | HIT / HIT | Same key-specific MP4 reused; no stage, encoder, probe, decode or publication occurred |
| Current open production editor's full proxy operation | NOT MEASURED | Live source database unavailable to read-only inspector; not classified as a miss |

The cache key covers the entire serialized timeline (including exact ranges, image/audio IDs/checksums, scene/motion provenance) plus renderer identity. UI tab, selected section, selected scene and unrelated AI provider settings are not direct inputs. Effective FFmpeg/FFprobe content or motion changes correctly change the key. No timestamp/random nonce is present in renderer identity. Freshly saving a genuinely different timeline or changing a motion preference is expected to miss. No accidental identity instability was observed. Current live motion preference could not be independently read from the locked database; the recorded preference reproduces the existing key exactly.

On a hit, **the MP4 itself is hashed once**, not multiple times by PreviewService. Initial and final currentness reread source media, not the proxy. On a miss, the output MP4 is fully hashed **three times** in this path: before full validation, after full validation, and before cache publication. Those checks guard different mutation boundaries; they cannot simply be deleted. Publication copies and fsyncs the bytes but does not hash the destination anew. Cache playback does not ffprobe or fully decode the MP4 before Qt.

Critically, `ProjectVideoRender.current` recompiles sources; TimelineCompiler resolves each clip twice to reject concurrent selection changes, and currentness then validates boundaries. `_is_current` does this twice per cached operation. **Therefore all 64 source scenes would be resolved/verified on an otherwise valid full-film cache hit**, plus image lineage and full narration WAV reads; this follows directly from unchanged source, not a measured production call count. Current edit retrieval also verifies retained timeline history on each gate. The synthetic harness uses fixed default motion config; production `compose_preview` additionally reads project motion settings through the catalog when requesting identity.

## E. Main-thread blocking and repeated UI work

Confirmed GUI-thread phase ownership was recorded in the full synthetic PreviewPanel run. Maximum 5 ms heartbeat gaps were **5.280 s cold, 7.415 s cached, 6.732 s repeat**. The two cached currentness calls occur in one coroutine iteration without an await between them, so the UI cannot paint/respond during their combined work. Synchronous cold readiness plus staging similarly blocks before the first native subprocess await. Button status “Starting proxy render” is therefore not evidence that FFmpeg has started or that the cache missed.

Large-synthetic readiness/staging were invoked on the owner/main thread and their durations are blocking function timings; a concurrent heartbeat was not recorded for those scaling calls. The production direct-play harness had 441.6 / 108.6 ms maximum heartbeat gaps, including checksum and native source setup. It had no live source checks.

| User operation | Verified behavior from current source | New timing / limitation |
|---|---|---|
| Open/reopen project | `load_project` composes services, binds TimelinePanel; refresh reads current history/candidates, draw selects boundaries and emits changed; editor forwards to `preview.timeline_changed`, which clears player and calls `is_current`; `_refresh` directly diagnoses and other signals queue more diagnoses | Production open not invoked. Earlier PERF-005 measured five diagnostic calls over load/event drain; not remeasured here |
| Refresh Timeline | current history + candidates + row boundary resolution + changed signal → preview currentness + export refresh + queued diagnostics + final-render readiness request | Source confirmed; independent full refresh timing NOT MEASURED |
| Change tab | Timeline entry has no special generic preview regeneration hook. Quality/Publish/Shorts tabs explicitly activate their services, which can perform their own verification | Source confirmed; no claim every tab runs diagnostics |
| Select narrative section | audio/plans/visuals/storyboard histories and progress refresh on GUI; `_select` does not directly call diagnose_pipeline | Earlier PERF-004 timings remain historical; not added to playback totals |
| Select timeline row | reads that clip's audio/timing boundaries; no full proxy recreation or player rebuild | Source confirmed |
| Select scene in preview dropdown | changes currentData; no connected automatic proxy generation/currentness callback | Source confirmed |
| Click film Play again | clears source; repeats both currentness gates, identity/key and full proxy hash; hits reuse MP4 | Newly measured above |
| Stop | player.stop only; no source/media validation | Source confirmed; separate restart via native player.play would avoid service work but panel's film button does not use that path |
| Visual or motion change | `timeline_changed` clears source and verifies timeline; editor explicitly runs diagnostics; motion change rebinds regeneration | Correct invalidation required; no production mutation used to benchmark |

Prior audit reconciliation: **PERF-002** full-catalog point reads and **PERF-004** nested global histories are still present and newly measured inside preview. **PERF-005** uncoalesced queued diagnostics remains visible (`QTimer.singleShot` for every request), but a bare film click does not itself invoke PipelineDiagnostics. Outstanding editor callbacks could add delay; that contribution is NOT MEASURED here. **PERF-007** is partly outdated: final rendering now uses frozen async readiness/staging, but preview still calls synchronous methods. **PERF-010** retained-media verification in other tabs is separate from the measured preview service. **PERF-011** output hashing/publication remains synchronous, now measured on the short fixture; executable hashing at approximately 1–4 ms is still a low-priority cost. Historical findings are from `reports/PERFORMANCE_AUDIT_2026-10.md`, not new production measurements.

## F. FFmpeg and Qt assessment

Current proxy profile: landscape **640×360**, portrait 360×640, 25 FPS, H.264/libx264 `ultrafast`, CRF 28, yuv420p, two encoder threads, one filter-complex thread; AAC 128 kb/s, 48 kHz mono; `+faststart`. These are plausible interactive proxy settings at the encoder output. Native process timeout defaults to 600 s; no timeout change is proposed without a measured production cold render.

The saved production timeline contains **64 2400×1350 motion masters and 16 unique narration artifacts**. For enabled controlled motion the filter first executes `scale=iw*2:ih*2,format=yuv444p` before zoompan outputs 640×360: **4800×2700 intermediate**, 56.25 times the output pixel count. The ratio describes raster size, not a measured CPU multiplier. Static motion uses a different center crop/scale. The 12-second moving-master synthetic encode took 7.141–10.214 s on separate runs. No real 627.8-second encode was launched. This establishes substantial cold-generation cost but not its exact production ETA or the exclusive scale/zoompan cost.

The command creates a separate looped PNG input and full WAV input for each clip; no audio-source deduplication. Staging also retains `.source` plus normalized PNG. In the real retained timeline the full section frame counts summed once per clip are 71,716,800 at 24 kHz: repeated complete section reads/copies are independent of a clip's short trim. No image-input dedup opportunity is asserted where all 64 selected images differ.

Existing MP4 diagnostics: FFprobe metadata **245.760 ms**, FFmpeg first-frame decode to null **222.104 ms**, complete video+audio decode to null **5.517 s**, all exit 0. Full decode is diagnostic only and is not part of cache-hit playback. MP4 atoms: `ftyp` at 0, **`moov` at byte 32 (354,754 bytes)**, `mdat` at 354,794. There is no end-of-file moov placement problem. Video is H.264 Constrained Baseline, 640×360, 15,695 frames; AAC mono; both streams 627.8 s.

Installed FFplay played the same proxy with bounded `-t 1 -an -autoexit`, exit 0, process lifetime **1.774 s**. Its log showed advancing video clocks; no exact first-presented-frame callback was captured. This is a video-only external comparison, not a claim that its startup is 1.774 s or directly comparable to Qt audio/video startup. No player was installed and no production application/player settings changed.

`ffmpeg -hwaccels` lists compiled CUDA, QSV, D3D11/12, DXVA2, AMF and other interfaces; that does not establish usable hardware encoding on this host. Qt emitted Media Foundation capability-discovery messages but no playback error. Hardware encoder/decoder selection and GPU utilization were NOT independently measured. There is no evidence supporting a codec/GPU switch as the first fix.

## G. Top five bottlenecks

| Rank | Finding | Evidence and impact |
|---|---|---|
| 1 | Redundant full source-currentness before/after cached proxy lookup | 7.355 s of the 7.998 s two-clip cache hit; complete source/lineage/audio rechecks despite valid MP4; 64-clip scaling in section C. High confidence |
| 2 | Nested whole-catalog point reads and repeated path/owner/connection guards | 468 scans / 3,048 SQL per two-clip cached operation; profile catalog cumulative 3.342 s of 4.413 s currentness. This is a nested cause of rank 1 and also affects staging; do not add the times |
| 3 | Synchronous staging with repeated whole-section copies and image preparation | 2.158 s for two motion-master clips, larger fixture scaling above; GUI remains blocked and cannot promptly dispatch cancellation before FFmpeg |
| 4 | Cold moving-image FFmpeg generation plus full output validation | Short actual encode 7.141–10.214 s, plus probe/decode 1.572 s in corrected run; moving high-resolution intermediate remains despite low-res output. Only affects misses |
| 5 | Reset/reload and synchronous proxy verification on every click | Actual 77 MB proxy hash 0.085–0.190 s; `_play` route→frame 0.426 / 0.136 s; repeat still empties source. Secondary compared with multi-second currentness. Published-output hashing/copy costs may grow but tiny synthetic output is not evidence of a large publication bottleneck |

## H. Smallest proposed fixes — no implementation performed

At most three focused PRs, ordered by expected benefit. These fit the desktop/service/storage boundaries and require no new orchestration architecture, provider changes, database migration or deletion of historical evidence. Detailed D### scope/dependencies must be selected before implementation.

| Proposed PR | Root cause / expected benefit / evidence | Complexity and effort | Correctness requirements and risks |
|---|---|---|---|
| 1. Indexed artifact point reads and scoped retained-history resolution | Remove all-manifest reconstruction for an exact ID/key and avoid resolving irrelevant history payloads before scope filtering. Targets rank 2 beneath both readiness/staging. Existing SQL PK/unique key indexes already support lookups; do not promise a production speedup multiplier | Medium, approximately 2–4 engineering days including behavioral coverage | Keep project ownership, containment, registration, manifest corruption/unknown-key behavior and legacy sidecars. Preserve full relevant selection chains, rejected variants and approvals; no fallback to historical selections |
| 2. Frozen, cancellable preview preparation with operation-scoped verification | Reuse existing owner-capture/worker pattern for currentness and stage; share dependency evidence only within an exact immutable operation, deduplicate identical immutable WAV/source preparation per render where possible. Existing worker run kept heartbeat gaps at 38/161 ms versus seconds. Lowers stalls and repeated work; UI responsiveness benefit is established, exact production latency gain unknown | Medium, approximately 3–6 days; snapshot/worker/late-result lifecycle is the coherent scope | Never hand SQLite to workers. Capture exact timeline/head/settings identity; reject changed selections after work and before presentation. Bound snapshot validity to the operation, check bytes at required trust boundaries, preserve cancellation/retained failures. Snapshot capture and final owner gates still need bounds; do not call frozen() as a magic zero-cost fix |
| 3. Reuse a verified current proxy/player presentation for unchanged playback | After valid dependency/integrity evidence, retain/restart the same source rather than empty-source reset and broad preparation every time. Use precise invalidation for timeline/media/settings/binary changes. Direct verified-cache result 0.670/0.341 s demonstrates the attainable player-side scale, not a promised complete fast path | Small–medium, approximately 1–3 days after verification boundaries are defined | No unconditional path-only shortcut or stat-only integrity guarantee. Revalidate when trust/currentness changes, retain full cryptographic verification at defined boundaries, guard file replacement, stale async results, device changes and project switch. Do not cache readiness forever or play stale proxy |

Requested optimization assessment:

| Option | Priority / assessment |
|---|---|
| Direct existing verified proxy | High after exact currentness/integrity evidence; PRs 2/3. Current function already reuses MP4 on HIT but recomputes dependencies |
| Avoid redundant MP4 SHA | Low for HIT: exactly one, 85–190 ms. On MISS three checks guard different boundaries; reuse only tightly scoped evidence with unchanged immutable bytes, never simply remove verification |
| Reuse scoped currentness evidence | High, PR 2; evidence keyed by project/revision/timeline/media/settings and invalidated on changes; no cross-operation assumption without a safe contract |
| Indexed lookups | Highest shared infrastructure benefit, PR 1 |
| Worker staging | High responsiveness benefit, PR 2; existing boundary measured |
| Deduplicate immutable source preparation | Medium within PR 2; one WAV used across multiple scene spans. Keep exact per-clip ranges and validation; linker/copy changes need portability and replacement safety review |
| Reuse generated proxy | Already confirmed for same key; improve gate/presentation reuse in PR 3 |
| Avoid player reset | Medium/secondary, PR 3; do not expect it to fix seconds of dependency scans |
| Lighter preview generation | Follow-up experiment, not one of the first three PRs. Measure equivalent-motion pre-scaling/filter strategies and decoded-frame visual equivalence before changing sampling or editorial appearance; no GPU/codec recommendation from availability alone |
| Decouple export readiness | Preview service already does not depend on final-render readiness. Do not add that gate. Editor-wide queued diagnostics/readiness can still contend for the GUI; coalescing diagnostics is supported by prior audit but unmeasured in this isolated click test |

Effort estimates are planning estimates, not measurements. Preserving checksums, provenance, currentness, immutable media and publication guarantees is a requirement for every proposal.

## I. Remaining unknowns, reproducibility and safety

1. **Full production A/B currentness and click-to-frame:** NOT MEASURED because the project was exclusively locked. Collect in the owning application with external/debug instrumentation or during an agreed closed-session window; do not stop the user's session, bypass locks or infer live selection from a retained file. Existing direct Qt results are explicitly partial.
2. No production miss/render was manufactured. Full-film encode, validation/publication, peak native RSS, sustained CPU/GPU utilization and physical disk I/O were NOT MEASURED. Do not extrapolate the flat synthetic images into a production ETA. Python CPU, instrumented file bytes, path profiles and native wall waits above have narrower meanings.
3. Loaded/Buffered/Playing/frame events were collected; actual screen scanout time and external FFplay first-frame latency were NOT MEASURED. Qt/media backend startup was not profiled internally. Isolated Qt timing includes the actual audio-route preparation; callbacks do not timestamp the instruction boundary inside native setSource.
4. Original short harness accidentally stopped waiting after service completion once total time exceeded two seconds, before Qt emitted its frame. Its first-frame totals are **not used**. Corrected independent cold/cached/repeat run waited for a valid video frame and traced all connection SQL; original run retained only as supporting encode/stage variability evidence. Scaling scan counts are valid; its late-trace SQL undercount is explicitly excluded.
5. Readiness and staging counts are not uniquely attributable exclusive times; cumulative profiles overlap. The synthetic catalog/history composition is smaller/different than production. Host I/O/antivirus competition and concurrent synthetic fixture preparation were not controlled, so repeated timings are descriptive rather than statistically stable estimates.

Evidence directory: `C:\Users\user\AppData\Local\Temp\aics-preview-audit-20261017`. Main artifacts: `blocked_real.json`, `player.json`, `hashes.json`, `cold_corrected.json`, `synthetic.json`, `worker.json`, `detail.json`, `currentness.prof`, `currentness-profile.txt`, `current-detail-profile.txt`, `stage-detail-profile.txt`, and corresponding external Python harnesses. Fixtures remain on C for review; no cleanup was performed. Command pattern: repository `.venv\Scripts\python.exe -B <external harness>`. Do not rerun creation harnesses against populated directories without selecting a fresh synthetic fixture path. Player harness uses only the named proxy and retained timeline; initial production currentness attempt stopped at the read-only lock failure.

No source code, production databases, project artifacts, final unpublished render, recovery backup, existing preview or cache files were modified by audit code. No paid API, narration/image generation service, complete production render, generic cleanup, Git reset/clean, package install, PR or merge was run. Only synthetic fixtures created by this audit were rendered. Report-only change: behavioral tests/full product suite are not applicable; final review uses source status and `git diff --check`. Stop after this audit; proposals require review before implementation.

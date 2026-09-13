# D010 — Audio for one immutable section

## Scope and dependencies

Implemented from master `9103465` (D040, PR #68), on `feat/d010-section-audio`.
D004 publication/recovery, D009 installed voice verification and D040 conditional
selection were inspected before changes. The initial focused baseline passed
95 tests. D002 and D008 retain their outstanding clean-Windows/manual gates.

The operation produces raw WAV for one explicitly selected section revision. It
does not generate an entire project, reuse preview caches, apply tempo, align
speech, plan scenes, change project language or add a provider. The managed
composition currently supports the approved Windows CPU Piper profile and an
explicit supported Polish catalog selection.

## Application and worker contract

`SectionAudioService` depends on injected voice/output ports and the D040
publication service. Enqueue records the complete immutable section snapshot,
catalog choice, technical chunk size and effective synthesis identity, including
runtime profile, worker-source revision and installed voice fingerprint. Current
section selection is checked by D040; retry retains the original job snapshot.

The logical raw output key is `section:<section_id>:audio:raw`. `SectionAudio`
reads the retained artifact's versioned metadata and exposes a measured duration
as `frame_count / sample_rate`. Other sections and previous variants remain intact.

Trusted composition:

```python
managed = ManagedSectionAudio(runtime, model_root, project_workspace / "work/section-audio")
service = SectionAudioService(publication, jobs, managed, managed)
queued = service.enqueue(section_revision, {
    "provider": "piper", "model": "pl_PL-gosia-medium",
    "voice": "gosia", "language": "pl",
})
worker = WorkerSupervisor(JobCoordinator(jobs), managed.worker_launch(),
                          completion_handler=service.complete)
outcome = await worker.run_next()
```

Run blocking validation/publication on the owning project coordinator outside
the UI thread, and await worker cleanup before closing its project session.
The queue must dispatch a section-audio job to this composition; a general
multi-operation regeneration scheduler remains later work.

The D007 supervisor supplies only configured workspace/model paths through trusted
launch environment. The private worker does not open project or queue databases.
It uses the existing catalog selection, TTS factory, `PiperTTSProvider`, technical
chunking and `ResumableChunkSynthesizer`. The injected `ManagedPiperBackend` binds
verified D009 model/config paths to the pinned Piper 1.6 `load` and `synthesize_wav`
API; it is not another provider. See the [upstream pinned Piper implementation](https://github.com/OHF-Voice/piper1-gpl/blob/v1.6.0/src/piper/voice.py).

The factory's existing exception clause referenced unimported provider exception
classes. D010 imports those classes so configuration failure reports
`TTSFactoryError` instead of `NameError`; an offline regression covers this path.

## Workspaces, interruption and publication

Each job uses a directory derived from its opaque ID under configured work storage.
Retries share that directory; different jobs/generations do not. An atomically
written request marker prevents reuse for a different snapshot. Work files are
separate from D004's immutable artifact store. Removing/reassembling a temporary
final WAV therefore cannot remove previously published media.

Chunk synthesis persists each verified chunk before reporting progress. Optional
progress/cancel callbacks preserve existing callers and allow D007 to cancel
between chunks; forced process termination remains D007's responsibility. Retry
reuses only chunks whose text/config identity, checksum and PCM parameters match.
Corrupt chunks are synthesized again. Changed runtime/model identities fail
closed, so an old job cannot silently resume using a different voice/backend.

After worker EOF, exit zero and cleanup, the coordinator independently validates:

- Frozen request marker, effective synthesis identity and completion manifest.
- Nonempty mono 16-bit PCM WAV, checksum, sample rate and measured frame duration.
- Exact expected technical chunk set, text/config hashes and each chunk's bytes.
- Compatible formats, cumulative frame count and concatenated PCM frame checksum.
- Completion counts and the catalog sample rate when the voice declares one.

The exact validated bytes become the D040 streaming publication source. Additional
`section_audio` metadata commits with the existing manifest/index; the publication
API rejects attempts to override reserved snapshot/dependency metadata. No new
database schema is introduced. D040 still handles obsolete revisions, competing
generations, duplicate completion and crash recovery. Worker-reported references
are not paths or selection authorization.

## Private runtime delivery

`worker_bundle.py` defines the explicit pure-Python source closure needed by the
private interpreter. D008 provisioning and its standalone build recipe consume
the same file map, preserving source bytes. An isolated-import test verifies the
closure without the checkout/site packages; a packed-source roundtrip verifies
the standalone data representation. Optional model libraries are still loaded
only inside the managed worker, and runtime dependency pins are unchanged.

D008 deliberately verifies exact application worker sources. A runtime installed
for an older application source revision is rejected; this task uses a fresh
verified installation, not an in-place update or an integrity bypass. Runtime
updates/migrations and the clean-Windows installer gates remain separate work.

## Validation

27 new offline cases cover measured duration despite false provider estimates,
retry/chunk reuse, corrupt chunks and final WAV, substituted same-length PCM,
identity/manifest/request mismatch, stale revision publication, unrelated sections,
history/reopen, duplicate completion, cancellation, a real process crash,
unsupported selections, missing voices, lazy factory/native API composition and
source-bundle isolation/roundtrip.

Final validation (2026-09-13, Windows, isolated Python 3.11.9):

- Focused suite: **145 passed in 33.08 s**, including 27 new D010 cases.
- `python -m pytest backend/tests`: **941 passed in 87.37 s**.
- `git diff --check`, new-file whitespace, documentation links and task-scope
  checks: **PASS**. Only the D010 task entry changed.
- Final managed Piper smoke: **PASS**, private interpreter provisioned from
  approved cached artifacts, existing verified D009 voice, actual D007 subprocess.
  Cancellation was acknowledged, retry completed with **2 reused / 6 generated
  chunks**, and published audio survived project reopen.
- Final WAV: **328704 frames**, **22050 Hz**, mono signed 16-bit PCM,
  **14.907210884353741 s**. SHA-256:
  `0441b55871f88321d0efb2ca9238fc7036b7b88c8d8883b18b5e4fade7a0a001`.
- Worker source fingerprint:
  `cbfc0c3e37ba2f00bc22999683afe32304cd093c5cd583ec83f0f6a1fccd75dc`.
  Local report: `.tmp/d010-managed-smoke-final/report.json` (ignored).

The source-bundle roundtrip was tested, but a new standalone Nuitka build and
clean-Windows/manual media acceptance were not run for D010. Existing D002/D008
Partial statuses remain unchanged.

Reproduce the explicit managed smoke outside default pytest/CI:

```powershell
python packaging/d010/smoke.py --runtime-root <fresh-ignored-runtime-root> --runtime-cache <verified-D045-cache> --model-root <installed-D009-model-root> --output <new-ignored-smoke-directory>
```

The smoke provisions from pinned cached runtime artifacts, uses the actual D009
Gosia voice, cancels generation after persisted chunk progress, retries through
D006/D007, publishes via D040 and verifies the artifact after project reopen.
WAVs, model files and runtime binaries stay outside Git. It is an automated media
integrity check, not a listening/prosody evaluation or clean-Windows release test.

The existing chunk synthesizer assembles one section in memory; D010 does not
claim constant-memory synthesis for arbitrarily long sections. D044 adversarial
path/race hardening, workspace garbage collection and runtime updates remain
outside this task. D011 has not been started.

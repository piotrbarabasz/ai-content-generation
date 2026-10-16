# D029 managed Chatterbox timeout recovery

## Evidence and root cause (2026-10-15)

The working tree was clean on integrated `master` before creating
`fix/chatterbox-voice-timeout-recovery`. No project database, selected audio or
script was changed. A read-only query of America 1776's job history found two
failed attempts with `worker_error: TimeoutError:` and last chunk progress 5/5
and 7/7 respectively.

Those records narrow the historical failures to post-terminal EOF/process-exit
waits. Handshake/send-run precede chunk progress; synthesis deadline exhaustion
used a different `worker_timeout` error. The old code stored no phase for its two
post-terminal waits, so distinguishing EOF from process exit retrospectively is
impossible. No claim is made about an exact CUDA teardown duration or native
library fault without new phase evidence.

The confirmed handle-lifetime bug is in `worker.serve()`: its quiet-worker finally
block restored the protocol descriptor onto stdout, keeping a protocol writer
open during interpreter/native teardown. Closing only the duplicate could not
signal EOF. The worker now leaves stdout redirected to stderr and closes the
protocol writer explicitly. The supervisor closes stdin after receiving the
terminal message, releasing the worker's cancellation reader, then still requires
EOF, no extra frames, zero exit code, complete pipe/process-tree cleanup and
independent WAV/manifest/chunk validation before publication.

## Observable phases and deadlines

Pipeline logs include job ID, attempt ID, PID, fixed phase name and elapsed
seconds. `WorkerRunResult.phase_durations` aggregates observed phase durations;
unobserved phases are omitted. Repeated chunk events do not create an unbounded
trace. Neither narration nor prompt/credential content is added to these logs.
The existing bounded stderr tail remains in force.

Observable phases are PROCESS_START, RUNTIME_INITIALIZATION, HANDSHAKE,
MODEL_LOADING, SYNTHESIS, FINAL_ARTIFACT_ASSEMBLY, TERMINAL_MESSAGE, STDOUT_EOF,
PROCESS_EXIT, CLEANUP and PUBLICATION. Initialization reports precede `ready` but
retain protocol version/job/attempt and owned-PID checks. Real Chatterbox model
loading is reported at the lazy local-model loader. Assembly reporting uses a
separate optional callback, preserving the existing persisted-chunk progress
callback contract.

| Code | Wait or condition |
| --- | --- |
| WORKER_HANDSHAKE_TIMEOUT | hello/run drain or ready/initialization |
| WORKER_EXECUTION_TIMEOUT | model loading, synthesis and assembly through terminal receipt |
| WORKER_TERMINAL_EOF_TIMEOUT | terminal stdin closure or stdout EOF |
| WORKER_EXIT_TIMEOUT | process wait after stdout EOF |
| WORKER_CLEANUP_TIMEOUT | process/pipe cleanup could not be confirmed within reap deadlines |

No Chatterbox timeout values were increased: handshake 90s, execution 900s,
exit grace 10s, reap 10s (cancel grace remains 2s). Execution is timed from the
accepted run, separately from handshake and post-terminal shutdown. Cleanup uses
the existing terminate/kill/reap policy and Windows process-tree containment.
Uncertain cleanup leaves the running claim unresolved and GPU lease quarantined;
it never authorizes publication or automatic retry. Explicit unload can finish
recovery after cleanup is confirmed.

## Retry, resume and progress

Only managed Chatterbox production composition enables one automatic retry.
Piper, previews and health probes keep their existing retry policies. Eligible
failures are the four operational deadline codes above, after confirmed cleanup
and process exit. A failed/canceled terminal outcome cannot become retryable just
because shutdown also times out. Configuration, handler, protocol, publication
and uncertain-cleanup failures are not automatically retried. Existing explicit
GPU OOM recovery is unchanged; devices are never substituted.

The supervisor checks the exact latest failed attempt, owned PID/return code,
pinned device and absence of unresolved ownership before `JobCoordinator.retry`.
The replacement retains the original immutable job request, publication generation
and hashed synthesis workspace. Compatible completed chunks are independently
revalidated and reused; failed/incomplete WAVs never become selected narration.
If clean shutdown fails after all chunks were written, retry can reassemble them
without invoking model synthesis again. Unclean completion is never promoted to
success merely to avoid retrying.

On explicit Run / Resume after a failed/interrupted/canceled job, production reuses the
latest same-output job only when its section revision, algorithm and prepared
voice/runtime/settings match exactly. A new manual run has a fresh one-retry
budget; a single invocation cannot loop indefinitely. Cancellation is checked
before enqueue, after cleanup and before starting the replacement worker.

Automatic previously bypassed `AudioPanel._tick()` and its readiness refresh.
It now refreshes diagnostics after each audio operation. Both VOICE SKIP and the
GUI counter use `selected_narration`, validating current source revision, voice,
PCM/checksum and measured metadata. A terminal worker message alone increments
nothing. Readiness is reconstructed from selected immutable artifacts on restart;
stale revisions remain inspectable but do not count. D067's complete measured
Standard narration check still precedes any scenes/images.

## Activating the updated worker

For an intact existing approved distribution, the subsequent D029
[explicit worker refresh command](CHATTERBOX_RUNTIME_REFRESH.md) now reuses its
verified interpreter and packages in a new generation. Use that operation for
worker-only application updates. The fresh-root provisioning procedure below
remains an alternative when reviewed dependency artifacts are available.

Existing D029 activation verifies the complete runtime inventory and exact
application worker source closure. These worker changes therefore require a
fresh verified runtime for this application revision. Do not patch an active
runtime, rewrite its receipt, relax verification or delete the old installation.
Runtime migration remains outside this repair; use the existing D029 provisioning
operation and the [documented fresh-root procedure](D062_SELECTABLE_IMAGE_GENERATORS.md#audio-runtime-isolation).

For the source launcher, with the app closed, an example using an existing reviewed
offline artifact cache is:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
.venv-ci311/Scripts/python.exe -c "from pathlib import Path; from app.runtime.chatterbox_distribution import load_approved_chatterbox_distribution; from app.runtime.chatterbox_provisioning import ChatterboxProvisioner; from app.runtime.provisioning import DirectorySource; ChatterboxProvisioner(Path('.runtime/d029-timeout-recovery')).install(load_approved_chatterbox_distribution(), DirectorySource(Path('.runtime/d029-wheelhouse')))"
$env:AICS_CHATTERBOX_RUNTIME_ROOT = (Resolve-Path ".runtime/d029-timeout-recovery").Path
python run_test_chatterbox.py
```

Use the actual approved artifact-cache location if different. Provisioning
revalidates all pins and performs its normal private CUDA health check; this is
an explicit local activation step, not a CI requirement. Existing model storage,
section-work cache and project paths stay configured as before. The distribution,
voice and synthesis identities are unchanged, permitting compatible frozen-job
recovery after activation. No local runtime was provisioned or modified as part
of the offline regression run.

## Offline regression evidence

Fault injection covers handshake/initialization, synthesis, terminal EOF, process
exit, successful delayed shutdown and uncertain cleanup. A production quiet-worker
fixture with slow `atexit` verifies EOF is closed before interpreter teardown.
Real lightweight child processes exercise cancellation, PID/process-tree cleanup,
lease release and quarantine without importing a GPU runtime.

Real queue/publication/chunk tests with fake workers prove one transient retry,
permanent failure and exhaustion, original-job recovery after restart, preserved
valid chunk cache, no duplicate publication, cancellation between attempts and
GUI header readiness after restart/source edit. An 18-section Automatic test
retains five completed recordings, resumes only missing sections, retries the
sixth once, refreshes counts 6 through 18 and stops at the measured-duration
guardrail before downstream generation. No paid APIs, real GPU, user-project
generation or image generation are used.

Validation results:

- Expanded focused lifecycle/audio/Chatterbox/GUI/workflow/duration suite:
  **179 passed**.
- Follow-up cancellation/resume and audio-service cases: **16 passed**.
- Existing image-resume acceptance with a verified artifact-index audio stub:
  **3 passed**, including its zero-paid-call assertions.
- Final full suite: **2,443 passed, 12 skipped** in **1,064.52 seconds (17:44)**,
  using `.venv-ci311/Scripts/python.exe -m pytest backend/tests --basetemp .tmp/ct3`.
- Working-tree and staged `git diff --check`: passed.

Changed production files: `runtime/supervisor.py`, `runtime/worker.py`,
`runtime/chatterbox_worker.py`, `runtime/section_synthesis.py`,
`desktop/audio_services.py`, `desktop/audio_composition.py` and
`desktop/pipeline_driver.py`. Regression changes are limited to lightweight worker
fixtures, lifecycle/GPU/audio recovery tests and the existing downstream
acceptance stub; no production image behavior changed. This note and the D029
task-boundary entry document the repair and activation prerequisite.

# D028 — Application-wide GPU resource lease

D028 implements a process-wide lease shared by managed workers from every project
and preview thread. One GPU operation is allowed at a time, even when callers name
different CUDA indices. `runtime.resources` imports neither Torch nor a provider;
availability reports scheduling ownership, worker PID and quarantine state without
probing hardware or promising free VRAM.

## Composition and identity

Compose `WorkerSupervisor(..., device=decision)` for a managed GPU worker. The
default `APPLICATION_GPU_RESOURCES` instance is shared across supervisors; isolated
managers are injectable for tests. This is the lease of one desktop application
process, not a system-wide scheduler for unrelated applications or other app
instances. Existing CPU Piper and remote OpenAI paths remain outside GPU dispatch.

`DeviceDecision` requires a profile identity, requested/effective device and the
devices tested by that profile's health check. Composition must obtain that evidence
from the trusted runtime profile. It does not infer CUDA availability from a model
name. Use `decision.bind(request)` **before enqueue/publication snapshot creation**;
the resulting `runtime_device` field participates in the immutable fingerprint.
Supervisor validates that identity before starting the child and includes the same
decision in `WorkerRunResult.device_identity`. Existing D040 artifact declarations
retain the request identity. A device-bound job cannot run in an unmanaged supervisor.

Selecting a different device requires `fallback_approved=True` and positive profile
health evidence for that device. Build a new bound request and enqueue it normally;
never mutate an old job or its artifacts. No provider substitution is performed.
D029 will supply the concrete Chatterbox health checks, device-aware worker and
hardware measurements. D028 tests use explicit fake health evidence.

## Ownership and cleanup

The supervisor reserves the GPU before claiming queued work. Contention returns
`None` with work still queued; callers can inspect manager availability and schedule
a later dispatch. Empty/paused queues and failed launches release the reservation.
A lease records its child process and rejects release while that process is alive.

Success, failure, crash, execution timeout and cancellation pass through the existing
D007 process-tree and pipe cleanup. Only then is the GPU released and the terminal
queue outcome recorded. This also unloads model tensors because the owning process
exits; `empty_cache` is not used as an unload mechanism. `await supervisor.unload()`
cancels active work and waits for exit. A later invocation starts a new worker.

If cleanup cannot be confirmed, ownership is quarantined and new claims are blocked.
An explicit `unload()` can retry cleanup and finish the unresolved attempt honestly.
No stale PID timeout automatically revokes ownership. Windows parent-crash handling
continues to use D007 kill-on-close Job Objects; reopen recovers the interrupted
queue attempt before retry starts a fresh child.

## OOM recovery

Workers report OOM with the existing protocol's `failed` event and code `gpu_oom`.
This becomes a durable failed attempt after exit. It causes no automatic retry.
`supervisor.retry_oom(result)` permits one explicit retry of the exact immutable job,
only after cleanup and only for its latest failed attempt. A second OOM refuses
further automatic-policy recovery. The ordinary user-controlled D006 retry mechanism
is unchanged; D028 does not impose a permanent ban on manual retries.

## Validation and remaining integration

Offline tests cover concurrent project/preview threads, two real project queues and
a private preview queue sharing the default manager, dead/stale ownership tokens,
crash, timeout, cancellation, failed launch, quarantined cleanup, one OOM retry,
restart success, explicit tested CPU fallback, device mismatch and reopen identity.
The real subprocess fixture allocates no GPU memory. Windows parent-crash tests
verify both unleased and GPU-leased workers and retry after reopen.

The private Piper worker source closure includes the new pure-Python module because
it ships D007 supervisor code. No model/runtime installation or hardware smoke is
part of D028. Measured GPU evidence belongs to D029 as specified in the backlog.
Work proceeds under the session's accepted exception for D025 release gates; those
gates remain open.

Validation on Windows with isolated Python 3.11.9 (2026-09-22):

- Resource-manager, GPU-worker and D007 lifecycle checks: **58 passed** in 20.59 s.
- Full `python -m pytest backend/tests`: **1595 passed, 11 skipped** in 617.37 s.
  Skips are the existing opt-in/optional checks; no real GPU smoke is claimed.
- `python -m compileall -q backend/app backend/tests`, `python -m pip check`
  and `git diff --check`: PASS.

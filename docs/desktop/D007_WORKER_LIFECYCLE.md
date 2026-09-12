# D007: versioned worker protocol and lifecycle

D007 connects one durable D006 claim to a private subprocess conversation. It
includes a harmless diagnostic worker, deterministic fault fixtures and a
standalone packaging smoke. It does not run models, provide an HTTP server,
implement a UI, schedule GPU resources or publish/select generated media.

D006 was merged at `778b854`; its queue and D002's existing packaged child code
were inspected and 40 dependency tests passed before implementation. D002 remains
PARTIAL because clean-Windows and human playback evidence is missing. D007 uses
and repeats its implemented packaged-worker boundary; no dependent UI work or
change to D002 acceptance is claimed.

## Protocol version 1

Private binary stdin/stdout pipes carry a four-byte unsigned big-endian length
followed by a UTF-8 JSON object. The body must be 1 through 262144 bytes. Reading
checks the prefix before allocating/awaiting its body, supports fragmented reads
and rejects truncated input. JSON must have unique object keys, finite numbers
and the exact supported envelope: `version`, `type`, `job_id`, `attempt_id`,
`payload`. Version must be integer 1, not a boolean. IDs are bounded opaque text;
artifact lists are limited to 256 IDs. This transports snapshots/references,
never media bytes, pickle objects or shell commands.

| Direction | Type | Payload/ordering |
| --- | --- | --- |
| parent → child | hello | Empty payload, exact job/attempt IDs |
| child → parent | ready | Actual child PID; matching version/identity required |
| parent → child | run | Frozen D006 job request/input snapshot, after ready |
| child → parent | progress | Phase, optional non-negative completed/total counts |
| parent → child | cancel | Empty payload, current job/attempt IDs |
| child → parent | completed | Opaque reported artifact IDs |
| child → parent | failed | Bounded error code/message |
| child → parent | canceled | Empty payload after cooperative cancellation |

A second handshake, wrong identity, invalid progress, unsolicited cancellation,
stdout log text, extra terminal event or nonzero exit fails the attempt. Receiving
`completed` is insufficient: EOF and exit zero must follow within the exit grace.
The D006 claim token stays in the coordinator; children cannot choose which
database attempt to update or write project metadata themselves.

## Coordinator and lifecycle

`WorkerSupervisor.run_next()` claims at most one queued attempt and returns a
`WorkerRunResult` containing the persisted outcome, PID/exit code and bounded
stderr tail. A paused/empty queue launches nothing. No automatic loop, retry or
second task is started. The caller owns scheduling of this async operation.

Pipe communication uses asyncio; database commands remain on the D003 session's
event-loop thread. Awaited I/O does not block other async work, and progress floods
explicitly yield so cancellation/deadlines remain responsive. This is an async
service boundary, not an implemented Qt/asyncio event-loop bridge. stderr drains
concurrently, retaining only the configured tail (64 KiB by default, at most
1 MiB); stdout frame buffering and outgoing writes are bounded.

Defaults: handshake/write deadline 5 s, whole conversation deadline 120 s,
cooperative cancel grace 2 s, exit grace 2 s and final reap deadline 5 s. Tests use
shorter explicit policies. A timeout or malformed message terminates the child
and records a failure. A queued cancel is handled by D006; an active cancel is
sent over the pipe after run, followed by forced termination if necessary.
Before run has been sent, cancellation terminates without starting the operation.
Late success after a recorded cancel cannot add result references.

`request_cancel()` and direct D006 cancel commands are supported. `close()` must
be awaited before closing the project. Canceling the supervisor's asyncio task
also cancels the attempt after cleanup, then propagates `CancelledError`.
Repeated task cancellation cannot interrupt cleanup. On failure/cancel, protocol
reading stops before stdout is drained; this releases paused Windows pipe
transports without accumulating unbounded output. A terminal queue state is
written only after the process is reaped and pipe tasks are joined. An exceptional
OS cleanup failure propagates and leaves the attempt for D006 restart recovery,
rather than falsely asserting a completed shutdown.

## Windows launch and containment

Composition supplies an explicit trusted native executable and, for source
execution, a fixed Python entry script. The script runs with `-I -u`. There is no
shell, arbitrary argument list or job-selected executable. Source composition
must use the actual interpreter (for example `sys._base_executable` in the test
driver), not the Windows venv redirector: handshake verifies the directly owned
PID. Compiled workers use the relocated EXE itself and no system Python command.
Runtime/model profile selection and environment allowlists remain D008/D045.

Windows processes launch with `CREATE_NO_WINDOW`. Before sending hello, the
supervisor attaches the child to a private Job Object with kill-on-close enabled.
Normal descendants created after attachment inherit containment. Closing the job
or losing the parent process handle terminates contained children, consistent
with [Microsoft's Job Object contract](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
Attachment failure prevents the handshake and fails the attempt. This is process
lifecycle control, not a sandbox for untrusted executables. A trusted worker must
wait for hello before starting work or spawning descendants. POSIX has direct
child termination fallback; no POSIX process-tree acceptance is claimed.

`runtime.worker.serve(handler)` provides one-job cooperative execution with a
cancel event and progress callback. The shipped entry supports only
`diagnostic.echo`, emits progress and returns no media references. Failure/hang/
malformed behaviors live in `backend/tests/fixtures/worker_process.py`, not in
user-selectable runtime code. Real providers and conditional publication remain
separate tasks; a reported artifact ID is not media validation or selection.

## Tests and acceptance

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Success/failure/hang/cancel produce truthful queue state | Real lightweight children; late success blocked; prior references retained | PASS |
| Malformed/version-mismatched replies fail safely | Bounded/truncated/invalid JSON, wrong IDs/PID, invalid ordering, nonzero exit | PASS |
| Cleanup releases child and pipe resources | Warning-as-error tests, forced/cooperative stop, double cancellation, Windows descendant and parent-crash tests | PASS |
| Application remains responsive during isolated work | Event-loop heartbeat during hung and stdout-flooding workers | PASS |
| Repeat packaged handshake from D002 | Relocated D002 EXE ping/exit zero, alongside standalone D007 handshake/queue completion | PASS |

The parent-crash test exits via `os._exit` with an active worker: the Windows Job
Object stops the worker, and reopening the D003 project marks its D006 attempt
`interrupted`. Tests also exercise stderr larger than a pipe buffer, command size
limits, frame fragmentation, unknown versions and honest no-outcome failures.

Focused validation: **91 passed**, including 51 D007 cases, 12.17 s.

```text
python -m pytest backend/tests/unit/test_worker_protocol.py backend/tests/integration/test_worker_lifecycle.py backend/tests/integration/test_durable_jobs.py backend/tests/unit/test_desktop_spike.py -W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning
```

Full validation: `python -m pytest backend/tests` — **723 passed**, 32.89 s.
`git diff --check` and new-file whitespace validation — PASS.

## Standalone packaging evidence

Use the existing isolated D002 build environment with Nuitka 4.1.1, Python 3.11
and MSVC 14.3. The D007 build stages only the diagnostic entry, worker and protocol
package and compiles a standalone folder. It does not package repository tests,
providers, developer environments or private data. The console-capable binary
preserves stdio; its supervisor hides the Windows launch.

```powershell
.\.tmp\d002-env\Scripts\python.exe packaging/d007/build.py --output build/d007/package-v2
.\.venv-ci311\Scripts\python.exe packaging/d007/smoke.py --bundle build/d007/package-v2/entry.dist --d002-bundle 'build/d002/package-v3/D002 packaging spike.dist' --output .tmp/d007-relocated-v2
```

Choose new output directories for reruns. The whole `entry.dist` directory is the
package. Compilation output goes to its ignored build directory's `build.log`.
The smoke copies both bundles under Unicode/spaces paths, launches from an
unrelated directory and restricts child PATH to Windows System32. Its JSON report
records D007 completion/exit zero and D002 ping/exit zero with actual child PIDs.

Recorded 2026-09-12 on Windows/Python 3.11:

- Bundle: `build/d007/package-v2/entry.dist/`, build exit 0.
- Report: `.tmp/d007-relocated-v2/report.json`, `automated_pass: true`.
- D007 EXE SHA-256: `77a2ab6d3294f65f46f9d56c93d4d1537ce1360f033e046aea2bb91139c1deb8`.
- D007 process PID 29240, exit 0; repeated D002 PID 29384, exit 0.

The developer host still has development tools. Restricted PATH is not a clean
Windows qualification, and this test does not observe media playback. D002's
status and its outstanding manual/clean-machine acceptance remain unchanged.

## Self-review and remaining limits

The changes are additive under runtime, tests and packaging. Existing queue,
project/artifact schemas, providers, UI and legacy worker behavior are unchanged.
The initial lifecycle test exposed an undrained stdout transport after failure;
cleanup now cancels its protocol reader, drains bounded chunks and awaits EOF.
The final focused checks treat resource/unraisable warnings as errors.

This task establishes the diagnostic execution boundary. Real models, GPU leases,
managed runtime provisioning, UI integration, media integrity and revision-aware
selection are not claimed. Trusted launch configuration must preserve isolation
and pre-handshake inactivity. Review should focus on cancellation/exit ordering,
process containment, protocol limits and queue event binding before merge.

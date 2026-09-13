# D008: managed private Piper CPU runtime

D007 and D045 are merged and their worker/profile tests pass. D008 implements
only the approved Windows x64 CPU profile. Its current result is **PARTIAL**:
the implementation and automated developer-host checks pass; the explicitly
required clean-Windows provisioning smoke has not been performed.

## Composition and installation

`PiperProvisioner(configured_runtime_root, host).install(profile, source)` is a
blocking application service. Desktop composition must call it off the UI/event
loop thread. `DirectorySource` supplies an explicitly prepared offline artifact
cache. The service reads only filenames in the approved manifest and rechecks
each complete artifact's size and SHA-256 before extraction. It does not invoke
pip, resolve dependencies, fetch URLs or select alternate artifacts. Obtaining
the exact public artifacts is a separate packaging/cache preparation step.

```python
from app.runtime.profile_catalog import load_approved_profile
from app.runtime.profiles import HostCapabilities
from app.runtime.provisioning import DirectorySource, PiperProvisioner

host = HostCapabilities("windows", "x86_64", ("cpu",))
profile = load_approved_profile("piper-cpu-windows-x64", host)
service = PiperProvisioner(configured_runtime_root, host)
installed = service.install(profile, DirectorySource(approved_artifact_cache))
# Existing D007 WorkerSupervisor accepts installed.worker_launch().
```

The shipped fingerprint from D045 is unchanged. An incompatible or unapproved
profile is rejected before storage writes or source access. Installation uses
an OS file lock released by process death and a new private `envs/<uuid>`
candidate. UUID directory names avoid unnecessarily long Windows paths; the
full profile fingerprint remains in receipts. The embedded Python archive and
seven approved wheels are installed without scripts, setup hooks or a copied
development venv. ZIP paths, special files, duplicate destinations and extraction
size/count limits are checked. The reviewed Piper wheel's COPYING data is
retained under `packages/share/`; unsupported wheel data schemes are rejected.

The embedded interpreter keeps a fixed `python311._pth` with only its stdlib ZIP,
private root, private packages and app-owned worker sources. It does not import
site, process `.pth` hooks, consult Python registry paths or use a user site.
This follows Python's [embedded distribution and path configuration](https://docs.python.org/3.11/using/windows.html#the-embeddable-package).
Worker launch uses the private executable and fixed entry point, an unrelated
private cwd, private temporary storage, and System32-only PATH. No environment
commands or import paths come from job data. Bytecode writes are disabled in
the entry point so successful worker execution does not alter install identity.

## Native libraries and worker health

`native_piper.py` contains one extraction recipe bound to the exact D045 Microsoft
redistributable SHA-256. It reads the reviewed attached CAB slice, asks Windows
`expand.exe` for only payload `a12`, verifies that x64 CAB's own SHA-256, and
extracts only the four required DLLs beside private `python.exe`. It never
executes the redist EXE/MSI or installs system software. Offset, payload identity
and hashes were verified against the approved binary; this is not a general
Burn reader. Windows [expand](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/expand)
supports selecting cabinet members. Microsoft documents [application-local DLL deployment](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files).

The private worker imports NumPy/ONNX/Piper before starting D007's cancel reader.
The actual Windows probe exposed a native NumPy initialization deadlock when
those imports ran concurrently with the blocking CRT stdin read; startup import
ordering removes that condition without modifying the D007 protocol.

The fixed `runtime.health` operation checks the exact private CPython version,
x64 architecture and isolated paths; all seven distribution versions and roots;
real NumPy/ONNX/Piper/espeak binding imports and CPU backend availability; and
the loaded paths of all four MSVC DLLs. Native module handles must resolve inside
the private installation even on a developer host with system MSVC installed.
No voice is loaded or downloaded and no audio is synthesized.

`piper_health` owns a hidden child and Windows Job Object, verifies the protocol
v1 handshake and owned PID, consumes exact bounded progress frames, requires
completion, EOF and exit zero, and kills/reaps on failure or deadline. stderr
is drained into a bounded tail; stdout is drained during cleanup to avoid a full
pipe preventing reaping. The typed result is bound to the approved fingerprint.
The worker also retains `diagnostic.echo` for D006/D007 composition checks.

## Publication and restart

A successful probe produces `ready.json` with health evidence and hashes of all
immutable installed files. Only then does `os.replace` publish the versioned
active-profile pointer. Source failure, malformed archives, native extraction
failure, failed health, interruption and pre-publication process death leave
the candidate inactive. A new invocation uses a new candidate; it never infers
activation from a populated directory. Failed health is retained separately.

`active(profile)` verifies the pointer, fingerprint, profile contents, complete
file inventory and current application worker sources without importing providers
or contacting a source. A valid installation is reused without installation or
another probe. Changed/missing/extra files or invalid receipts fail closed.
Private `temp/` contents are excluded from identity. Relative receipts permit
storage relocation; an explicit probe can recheck the relocated environment.

Receipts detect incomplete/corrupted local state; they are not protection against
an attacker who can rewrite both runtime bytes and receipts. Atomic publication
and restart verification cover process interruption; this is not a claim of
power-loss durability on every filesystem. Failed candidates and crash scratch
directories are retained and consume disk space. Automatic garbage collection,
repair, update/rollback and arbitrary profile installation are outside D008.

## Validation and acceptance

| Criterion | Evidence | Result |
| --- | --- | --- |
| Worker runs without system Python/Piper | Standalone installer, private pinned interpreter/packages, real native imports, private DLL origin checks, relocated worker, System32-only PATH; D006/D007 diagnostic completes with exit zero | PASS on developer host; clean Windows pending |
| Failed installation remains inactive | Offline hash/size, interruption at four stages, failed health, real process-death and lock-release tests | PASS |
| Restart recognizes the same installed profile | Exact generation reuse without source/probe, corruption rejection, relocation and post-job inventory checks | PASS |
| Explicit clean-Windows provisioning smoke | Standalone recipe and operator commands provided below; Windows Sandbox absent and Hyper-V access denied in this session | NOT RUN |

Default tests use synthetic offline archives and lightweight child processes;
fixture profiles are trusted only through test-local allowlist overrides. No
default test imports real Piper/ONNX/NumPy, uses downloaded archives or connects
to a provider. The explicit packaging smoke uses D045's already approved public
artifacts from an ignored local cache.

Focused command (isolated Python 3.11.9):

```text
python -m pytest backend/tests/unit/test_runtime_provisioning.py backend/tests/integration/test_piper_health.py backend/tests/unit/test_runtime_profiles.py backend/tests/unit/test_worker_protocol.py backend/tests/integration/test_worker_lifecycle.py backend/tests/integration/test_durable_jobs.py -W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning
```

Result: **167 passed**, 24.89 s, including 42 new D008 cases. Final full-suite
counts are recorded in the D008 entry of `IMPLEMENTATION_PLAN.md`. No other
D### status is changed.

The final standalone smoke passed on Windows build 22631 with Nuitka 4.1.1 /
MSVC 14.3. The entire bundle was relocated to a Unicode/space path and launched
from an unrelated cwd with System32-only PATH. The installer, both private
worker probes, restart and runtime relocation passed. The clean-Windows flag
was false. A separate D006/D007 run completed `diagnostic.echo`, exited zero,
and retained the exact installed inventory afterwards.

Ignored local evidence:

- Bundle: `build/d008/package-final/smoke.dist/` (copy the entire directory).
- Executable SHA-256: `56d80bba6b5d54ab1599cf5633f06b3ee4763d03d5ca7e2cd956a4fc0ef86cc1`.
- Build log: `build/d008/package-final/build.log`.
- Final report: `.tmp/d008-packaged-final/report.json`.
- Smoke log: `.tmp/d008-final-smoke.log`.

No runtime archives, DLLs, private environments or build outputs are tracked.

## Repeating the clean-Windows gate

Build with the existing pinned `packaging/d002/requirements.txt` toolchain:

```text
python packaging/d008/build.py --output build/d008/package-final
```

The build stages only stdlib application modules and the approved profile JSON.
Its `worker_sources.json` preserves the three current app-owned worker source
files for the private interpreter; application code itself is compiled by Nuitka.
Copy the **entire** `smoke.dist` directory and all nine exact approved artifacts
to a disposable Windows x64 VM with no Python, Piper or VC redistributable.
Use a new writable output directory, preferably a short root with Unicode/spaces.
Disconnect network access, limit PATH to Windows System32, and run:

```text
d008-smoke.exe --artifacts <approved-cache> --output <new-smoke-directory> --clean-windows
```

The `--clean-windows` flag records the operator's VM attestation; it does not
detect or certify a clean host. Preserve `report.json`, OS/build identity and
the executable SHA-256 with the VM evidence. Success checks installation,
restart, source-free idempotence, relocation and a second private worker probe.
Do not set the flag on a developer computer. This gate must pass before D008
is marked complete. Voice/model readiness and synthesis remain D009/later work.

Review the narrow cabinet recipe, path isolation, activation boundary, retained
failed-install disk use and clean-machine report before merge. No merge or next
task is performed by this implementation run.

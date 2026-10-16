# Explicit managed Chatterbox worker refresh

An application update can change its reviewed worker source closure. Startup
deliberately rejects an installed closure that differs from the current
application, even if the approved interpreter and dependencies are unchanged.
The operator command below repairs this mismatch without downloading dependencies
or weakening startup verification. It belongs to D029's runtime lifecycle repair
boundary; no other D task is selected.

## PowerShell commands

Run from the repository with the target application revision checked out. Close
the application and finish any managed GPU work before refreshing. The example
uses the existing isolated Python 3.11 application environment. Retain the current
model and writable cache settings in the root `.env`.

```powershell
Set-Location D:\Projects\ai-content-generation
$env:PYTHONPATH = Join-Path $PWD 'backend'
$runtimeRoot = Join-Path $PWD '.runtime\d029-managed-d065'
$python = Join-Path $PWD '.venv-ci311\Scripts\python.exe'

# 1. Read-only: verify historical integrity and list exact worker differences.
& $python -B -m app.runtime.chatterbox_refresh --root $runtimeRoot --check
if ($LASTEXITCODE -ne 0) { throw 'Runtime inspection failed; do not refresh.' }

# 2. Explicit offline refresh. Defaults to the installed health device.
& $python -B -m app.runtime.chatterbox_refresh --root $runtimeRoot
if ($LASTEXITCODE -ne 0) { throw 'Runtime refresh failed.' }

# 3. Reopen the activated installation in a fresh isolated application process.
& $python -B -m app.runtime.chatterbox_refresh --root $runtimeRoot --verify
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }

# 4. Launch the test desktop. Existing model/cache settings remain in .env.
$env:AICS_CHATTERBOX_RUNTIME_ROOT = $runtimeRoot
& $python run_test_chatterbox.py
```

Use `--device cuda:0` on the refresh command only if explicitly selecting that
CUDA device instead of the device recorded in the old installation's health
receipt. The existing managed probe checks the candidate's private interpreter,
packages, pinned V3 source/API, worker protocol, owned process exit and CUDA
device. It does not load model weights or synthesize narration.

`--check` returns success for both a current closure and an intact outdated
closure; its output distinguishes them and lists added, removed and changed
worker paths. Integrity failures return exit code 1. It creates no runtime
directories, locks, receipts, caches or processes. `--verify` performs strict
current-worker verification in a fresh process without a GPU health probe.
Inspection and verification hash the entire protected installation; allow time
for large runtime inventories. Refresh also copies all verified dependency bytes.

## Verification and activation

The command validates the approved distribution fingerprint, active pointer,
original health receipt, profile and complete protected inventory before
permitting refresh. A modified worker whose bytes fail its old receipt is
corruption, not an eligible application update. Corrupt dependencies, profile
changes and links/reparse points are rejected as well.

Refresh takes the existing provisioning lock and checks free disk space for a
complete independent copy plus 512 MiB headroom. It copies each non-worker file
with exclusive creation, byte copying, fsync and hash validation. It installs
only the current `_worker_files()` closure, dropping obsolete worker paths in
the candidate. It creates neither hardlinks nor symlinks. All non-worker hashes
must remain identical before and after health checking. Scratch is private to
the candidate's excluded `temp` directory. The original generation is rechecked
before a new receipt records the actual hashes and CUDA health evidence.

A fresh application process verifies the candidate before the atomic pointer
swap. Another fresh process reopens the newly active generation using the same
strict `ChatterboxProvisioner.active()` gate as desktop startup. A failure or
KeyboardInterrupt during this post-activation check restores the exact previous
pointer bytes. An abrupt process/power loss at the atomic commit can leave either
the previous generation or the fully verified new generation active; it cannot
publish a partial candidate. Incomplete candidates and old generations remain
available for inspection; this command does not delete them. Repeated refresh
of a current runtime verifies it without copying or another health probe.

The command has no model/project/output arguments and does not touch those
stores. No refresh is called by startup. This operation does not update the
approved dependency distribution; a changed dependency profile requires normal
reviewed provisioning.

Read-only inspection of `.runtime/d029-managed-d065` accepted the original
receipt and inventory for generation `7d429dc360cf464e8102c08cdefcb1ce`. It reported
four changed source files (`chatterbox_worker.py`, `section_synthesis.py`,
`supervisor.py`, `worker.py`) and 55 receipted bytecode files absent from the
current closure. Refresh drops those old worker bytecode files in the candidate.
Managed Python launches now explicitly use `-B`: isolated `-I` mode ignores the
previous `PYTHONDONTWRITEBYTECODE` environment setting. This suppresses Python
bytecode writes. Initial implementation validation only inspected the live
installation; the subsequent operator refresh is recorded below.

## Explicit rollback

Retain the previous generation ID printed by `--check`. Rollback is permitted
only when that generation's original receipt and inventory are intact **and its
worker closure exactly matches the target application**. To return to a previous
application version, check out that version before activating its generation.
If that revision predates this command, use an application revision with this
command and the matching worker closure, or its original approved provisioning
workflow. Never patch a historical receipt or bypass worker checks.

```powershell
$previousGeneration = '<32-character generation ID from check output>'
& $python -B -m app.runtime.chatterbox_refresh --root $runtimeRoot --rollback $previousGeneration
if ($LASTEXITCODE -ne 0) { throw 'Rollback refused; active pointer preserved.' }
& $python -B -m app.runtime.chatterbox_refresh --root $runtimeRoot --verify
```

Rollback is explicit, locked, verified in fresh processes before and after atomic
activation, and never rewrites either generation. An old closure cannot be
activated for a newer application merely because its dependencies match.

## Offline acceptance

Synthetic runtime fixtures exercise real receipts and inventories, exact worker
differences, current/no-op behavior, independent copies, obsolete worker removal,
fresh isolated Python verification, corrupt files/fingerprints, insufficient
space, copy interruption, health failure/tampering, post-activation recovery,
startup fail-closed behavior, external project/model/audio preservation,
idempotence and explicit version-compatible rollback. No GPU or network is used
by these regression tests. A live refresh remains an explicit operator action.

Initial validation on Windows, isolated Python 3.11.9:

- Final focused refresh/lifecycle/GPU suite: **76 passed**.
- Fixture-only alternate host-gate path: **19 passed**, using a simulated
  non-x64 host and real Python verification children. Production host checks
  remain unchanged; this is not a real Linux or ARM hardware run.
- Full `python -m pytest backend/tests --basetemp .tmp/crf2`:
  **2,462 passed, 12 skipped** in **1,813.89 seconds (30:13)**.
- New modules compile successfully; working-tree and staged `git diff --check`
  pass. No live generation was copied, activated or health-probed.

## Follow-up live cache correction

The first explicit live refresh failed closed on a 90-second initialization
deadline. An offline import diagnostic subsequently completed in 61 seconds,
with most time in Transformers package discovery and imports. A second normal
refresh completed health but was correctly rejected by the protected inventory
comparison: librosa/Numba rewrote six `audio._zc_wrapper` / `guf-audio._zc_wrapper`
`.nbi` and `.nbc` files in `packages/librosa/core/__pycache__`. Both attempts left
the original active pointer and generation intact. Neither candidate received a
ready receipt.

`-B` does not control Numba's native-code cache. The shared private cache
environment now sets `NUMBA_CACHE_DIR`: health redirects JIT writes into its
excluded temporary scratch; production redirects them into its configured
writable runtime cache. All original package/cache bytes remain protected and
must still match the old receipt. No deadline or package/model version changes.

Numba's hashed source directory and atomic-write suffix also exceeded Windows'
path limit in the original nested health directory. Health now uses the short
`cb-*/n` cache layout within the same temporary ownership boundary. An actual
private CUDA probe then passed on the GTX 1660 SUPER (`cuda:0`, EN/PL), with
35.797 seconds of initialization and clean process exit. No weights were loaded.

Focused refresh/distribution/audio/recovery regression after these corrections:
**45 passed**. A real child-process regression simulates import-time JIT writes,
checks that inherited/protected cache paths are overridden, preserves the
receipted package bytes, exercises Numba-shaped hashed/atomic filenames under a
deterministic Windows path budget, and verifies temporary-cache cleanup.
Production cache selection is also checked to remain under the configured
writable root.

The subsequent complete operator refresh activated generation
`7a9066c51ff448429b4592afc3e80b0a` successfully, including strict post-health
inventory comparison, original-generation integrity verification and fresh
process checks before and after activation. An additional `--verify` using the
user's `.venv` interpreter also passed. Original generation
`7d429dc360cf464e8102c08cdefcb1ce` remains intact; rejected candidates remain for
inspection. Model weights, project databases and completed narration were not
modified by this runtime operation.

Final follow-up validation: `python -m pytest backend/tests --basetemp .tmp/ncf`
completed with **2,464 passed, 12 skipped** in **1,998.49 seconds (33:18)**.
Working-tree and staged `git diff --check` passed. All tests are offline; the
separately invoked live health/activation evidence above does not synthesize
narration or load model weights.

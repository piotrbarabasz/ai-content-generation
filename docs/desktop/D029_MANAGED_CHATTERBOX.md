# D029 — Chatterbox V3 managed runtime

Status: **Completed** (2026-09-22). The candidate contract is now backed by an
approved, reproducible private Windows/CUDA distribution, restart-verified
activation, explicit installed-product selection and retained real EN/PL hardware
evidence. The Piper/D045 distribution remains independent and unchanged.

## Implemented boundary

- `runtime.chatterbox_profile` pins the Python/CUDA/core-package combination,
  upstream source commit, immutable model snapshot, six asset sizes/SHA-256 values,
  built-in voice and the existing English/Polish product language boundary.
  Discovery imports neither Torch nor Chatterbox.
- `ChatterboxAssets.install(source)` explicitly copies public assets into configured
  model storage, verifies bounded size and SHA-256, and atomically publishes a
  complete immutable version. `ChatterboxDirectorySource` can read a selected local
  Hugging Face snapshot. Every reuse verifies the bytes; missing/corrupt files fail
  closed. Cancellation, insufficient space and corrupt inputs never activate a
  candidate. Incomplete candidates remain available for diagnosis. The pinned
  Cangjie file is also materialized into the exact private Hugging Face cache layout.
  Intake does not support partial-file range resume or automatic HTTP download.
- `check_private_runtime` runs only in the private child. Approved launches check
  private interpreter/package locations, all 111 exact package versions and source revision, V3 API,
  CUDA version/index and the supported source languages. It loads no model and
  allocates no model tensors. `probe_chatterbox` supervises this bounded operation
  with D007, reserves D028 ownership before CUDA initialization (without asserting
  positive health in advance), and reads a fixed, size-bounded report in configured scratch storage.
  A health result is **not** approval of arbitrary executable content.
- The synthesis worker calls the existing adapter through an injected
  `from_local(..., t3_model="v3")` backend. It never calls `from_pretrained` or
  fetches mutable `main`. Offline flags and a Python network-denial audit hook are
  installed before optional imports (this is not a native-code security sandbox). The
  Hugging Face, Torch, pkuseg and temporary caches are rooted in configured work
  storage; global user caches are not evidence of private-runtime readiness. The
  existing D010 chunk manifest and D040 publication remain authoritative.
- `CandidateChatterboxAudio` implements the existing voice/output ports. Its
  constructor is a trusted composition boundary requiring an explicitly supplied
  private launch, health and complete distribution fingerprint, not a project
  setting or runtime-registration API. Profile, distribution, model and effective
  device identity are frozen before enqueue and checked before publication.
- `compose_candidate_chatterbox_audio` connects production and preview to the same
  D028 manager. Both use the existing catalog/selection contract and built-in
  voice. No CPU fallback, speaker-reference intake, alternate provider or XTTS
  policy change is introduced. `compose_installed_chatterbox_audio` is the separate,
  explicit installed selector and returns unavailable unless both the approved
  active runtime and verified model snapshot exist. Default Piper composition stays intact.
- Native and Python library logs are redirected away from the framed stdout
  protocol; imports occur before the blocking cancel reader. CUDA OOM from loading
  or generation becomes `gpu_oom` after preserving completed chunks. Further model
  calls in that worker are refused. D028 owns exit/unload, quarantine and the one
  explicit OOM retry. Each subsequent run loads a fresh model in a new process.
- Successful private synthesis writes diagnostic peak allocated/reserved byte
  counts alongside its work files. These are not accepted as artifact paths or as
  proof of hardware acceptance by themselves.

## Pins and provenance

The recorded manual matrix in [Chatterbox setup](../tts/CHATTERBOX_SETUP.md) is a
starting point, not D029 packaged acceptance. The candidate uses Python 3.11.9,
Torch/torchaudio 2.6.0+cu124, CUDA 12.4, Chatterbox 0.1.7, resemble-perth 1.0.1,
setuptools 80.10.2, transformers 5.2.0 and NumPy 1.26.4. This list is the **core
compatibility contract**, not a complete dependency lock or install command.

The upstream [fixed source revision](https://github.com/resemble-ai/chatterbox/blob/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/mtl_tts.py)
exposes the required V3 local-loader API. A version string alone does not identify
that source tree. We retain the existing adapter instead of replacing its model or
silently selecting V2/Turbo.

Weights are pinned to the public [model snapshot](https://huggingface.co/ResembleAI/chatterbox/tree/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18).
LFS hashes and lengths came from the publisher's snapshot metadata. The two JSON
files were hashed locally and their Git blob IDs matched that same snapshot:
`d27fb3f2fd38ca39b7bbfbf83a13b3c617e551df` (graphemes) and
`d77891f84ca1db0d6f7058a4ee081d4bb0bfe88e` (Cangjie). The shipped code contains only
metadata, never weights or private reference audio.

## Completed D029 acceptance gates

1. `chatterbox_gpu_windows_x64.json` records the complete reviewed CPython 3.11
   Windows x64 closure: 111 wheels with exact bytes/provenance/license evidence,
   37 Torch/CUDA DLL hashes, the pinned interpreter and MSVC runtime, and the
   `spacy_pkuseg` OntoNotes archive. The reproducible Chatterbox wheel is built from
   the fixed source commit and normalized without changing its Python sources;
   its mutable dependency declaration is replaced by the reviewed exact Perth
   version. Cangjie and pkuseg are provisioned before offline execution. The
   separate content fingerprint is allowlisted; the Piper v1 validator was not widened.
2. `ChatterboxProvisioner` verifies every input byte, performs bounded non-executing
   wheel extraction, verifies the reviewed native-library inventory, writes source
   provenance, runs health in the embedded interpreter and only then atomically
   activates the generation. Restart discovery rehashes the installed inventory,
   worker sources and profile content. The installed runtime was created from the
   reviewed wheelhouse, not copied from `.venv-tts311`, and a fresh application
   process recovered the same trusted launch and distribution fingerprint.
3. The repeatable `packaging/d029/validate_hardware.py` run passed on Windows with
   an NVIDIA GeForce GTX 1660 SUPER (6,442,123,264 bytes reported device memory).
   English produced 2.80 s / 24 kHz audio with SHA-256
   `fd9a8c1bf13956025dc80dc6cb14a65c727ea66a21230e2fc7137b27669e803f`;
   Polish produced 7.92 s / 24 kHz audio with SHA-256
   `a68e05d4c14be839b4492d324c380329a7b002c8a5925df537d8663f60b72e1a`.
   The Polish first attempt was canceled after one chunk and the restarted worker
   reused that chunk while generating two. Concurrent preview was rejected while
   production owned `cuda:0`. Peak reserved bytes were 3,458,203,648 (EN) and
   3,477,078,016 (PL); after unload no private worker remained and D028 reported
   the GPU available.

Read-only inspection of the existing development `.venv-tts311` found matching
Torch/torchaudio and Chatterbox source, but setuptools **65.5.0**, not the documented
80.10.2, and a venv rather than a private embedded interpreter. It was not modified,
imported into the application or used as proof of managed runtime readiness.
The accepted runtime was built separately from exact cached publisher/build artifacts.

## Validation and retained evidence

Tests use fake assets, health modules and model outputs. Real subprocess tests cover
framed IPC, native/Python log isolation, publication/reopen, reuse of completed
chunks, preview identity, contention and bounded OOM retry. Separate handler tests
exercise the actual local-loader bridge with fake native OOM, and health tests
reject changed source/version, missing CUDA/index/language and non-private Python.
These tests complement, rather than substitute for, the real hardware run above.

Windows / isolated Python 3.11.9 validation (2026-09-22): focused D029
distribution/profile/health, provisioning, section/preview and installed-selection
tests: **55 passed**. `python -m compileall -q backend/app backend/tests
packaging/d029`, `python -m pip check` and `git diff --check`: PASS.

Full `python -m pytest backend/tests`: **1640 passed, 11 skipped in 367.65 s**.
The hardware evidence directory is intentionally outside Git at
`.runtime/d029-evidence-20260922-194850/`; `evidence.json` records the project tree
hash, audio hashes/durations, complete package map, source/model revisions, timing,
peak VRAM, contention result and post-exit ownership. The reviewed wheelhouse,
installed runtime, models and synthesized project/audio also remain outside Git.

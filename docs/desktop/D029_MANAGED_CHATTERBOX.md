# D029 — Chatterbox V3 managed-runtime candidate

Status: **Partial**. The candidate contract, verified model intake, private worker
and explicit audio composition are implemented and tested offline. This is **not**
an approved/installable runtime and does not enable Chatterbox in the installed
product. D029 must not be marked complete until the distribution and measured
hardware gates below pass.

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
  candidate. Incomplete candidates remain available for diagnosis. Intake does not
  yet support partial-file range resume or automatic HTTP download.
- `check_private_runtime` runs only in the private child. It checks private
  interpreter/package locations, exact core versions and source revision, V3 API,
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
  policy change is introduced. Default installed composition stays Piper-only.
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

## Remaining D029 acceptance gates

1. Produce and review the complete Windows x64 private-runtime dependency closure,
   exact downloadable/buildable artifacts, hashes, native libraries and licenses.
   Include auxiliary tokenizer data: the fixed upstream tokenizer constructs a
   Chinese converter even for English/Polish, calls Hugging Face for Cangjie data,
   and initializes `spacy_pkuseg`, which may fetch its segmentation model. Six main
   checkpoint files alone are not a complete isolated/offline runtime. The worker
   now refuses such implicit connections; the auxiliary assets/cache layout need
   explicit provisioning and verification before activation.
   The source build must preserve the pinned commit despite its shared 0.1.7
   version. The current D045 v1 validator/allowlist deliberately admits only the
   Piper CPU distribution; it has not been weakened to accept arbitrary manifests.
2. Implement approved Chatterbox distribution provisioning/activation with those
   pins, including restart verification. Supply its trusted launch and content
   fingerprint to the candidate composition. No development venv may be copied or
   adopted as an installed product runtime. Add explicit installed-product
   selection only after this gate, preserving the working Piper path.
3. Run real Windows/GPU source-language synthesis, interrupted-chunk resume,
   preview/production contention and unload/restart. Retain the project, audio
   checksums, package/source/model versions, durations, measured peak VRAM and
   post-exit ownership evidence outside Git. Test English and Polish separately;
   the upstream language list is not measured audio-quality evidence.

Read-only inspection of the existing development `.venv-tts311` found matching
Torch/torchaudio and Chatterbox source, but setuptools **65.5.0**, not the documented
80.10.2, and a venv rather than a private embedded interpreter. It was not modified,
imported into the application or used as proof of managed runtime readiness.
No real model/GPU synthesis or large asset download was performed in this change.

## Offline validation

Tests use fake assets, health modules and model outputs. Real subprocess tests cover
framed IPC, native/Python log isolation, publication/reopen, reuse of completed
chunks, preview identity, contention and bounded OOM retry. Separate handler tests
exercise the actual local-loader bridge with fake native OOM, and health tests
reject changed source/version, missing CUDA/index/language and non-private Python.
They do not stand in for any remaining gate above.

Windows / isolated Python 3.11.9 validation (2026-09-22): focused candidate,
health, section/preview, GPU ownership, D007 lifecycle and Piper provisioning
regressions: **131 passed in 41.72 s**. `python -m compileall -q backend/app
backend/tests`, `python -m pip check` and `git diff --check`: PASS.

Full `python -m pytest backend/tests`: **1631 passed, 11 existing optional tests
skipped in 640.81 s**. The D029 hardware/distribution gates remain pending despite
the green offline suite.

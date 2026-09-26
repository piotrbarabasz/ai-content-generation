# D059 local image upscaling evidence

## Profile

- Model: official `xinntao/Real-ESRGAN` `realesr-general-x4v3.pth`, release `v0.2.5.0`.
- SHA-256: `8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292` (4,885,111 bytes).
- Architecture: SRVGGNetCompact, 32 convolution blocks, native x4. The worker adapts the official BSD-3-Clause architecture; attribution and license are retained in `backend/app/runtime/REALESRGAN_LICENSE.txt`.
- Runtime: separately managed Windows CPython 3.11.9; pinned `torch==2.6.0+cu124`, `pillow==11.1.0`, `numpy==2.4.6`. The private runtime inventory, model hash, package versions and worker source are verified at activation and restart. Installation adopts explicit offline sources. Mutable caches are outside the verified inventory. D057 files are never edited.
- CUDA device 0, FP32, tile 128, tile padding 10, pre-padding 0, denoise strength 1.0. No WDN or face model.
- Factors: 2 and 4. The 2x output is native x4 inference followed by **Pillow Lanczos** downsampling; it is not a native x2 model. The final resize choice is recorded in diagnostics.
- No CPU fallback, automatic weight download, or inferred tile change.

## Configuration and explicit intake

Set `AICS_UPSCALE_PROVIDER=local` and `AICS_LOCAL_UPSCALE_ROOT` to a separate managed root. Example manual acceptance root: `D:\AI Content Studio\upscaler`. Product code does not hard-code this path.

With the repository Python 3.11 environment active:

```text
python -m app.runtime.upscale_runtime install <managed-root> <private-python-source> <official-model-file>
python -m app.runtime.upscale_runtime verify <managed-root>
```

Normal startup and inference do not download weights or packages. The optional provider is absent unless configured. The provider acquires the D028 GPU lease before launching a one-shot worker and releases it only after process cleanup. Worker inference receives image bytes and returns PNG bytes; only the coordinator publishes artifacts and changes selection.

## Artifact behavior

An upscale uses the exact selected source artifact ID and checksum in its fingerprint, together with factor, model, runtime, tile, dtype and algorithm profile. The derivative records its source artifact ID/checksum/dimensions and output dimensions. The original remains retained. Publication and selection happen after complete PNG decoding and size validation. A stale selection prevents publication. Cached derivatives can be selected again without inference.

## Acceptance record

Offline tests cover the contract, factors, generated/imported lineage, cache, output validation, failure, stale selection, GPU contention, OOM and cancellation cleanup, Visuals dimensions, project reopen and timeline selection. A real provider-to-project smoke imported the existing successful D057 PNG, published a distinct 1024×1024 derivative, switched to the original and back, and verified both variants and derivative selection after project reopen. A Windows Qt offscreen smoke then selected the retained 512×512 source in Visuals, displayed 2048×2048 before execution, upscaled through the real CUDA provider, published and selected a distinct 2048×2048 variant, loaded its preview pixmap, and counted 378 GUI event-loop ticks during background work. The scene held three retained variants. D059 remains Partial until interactive on-screen review and render playback are observed.

Focused D059, Visuals, timeline and local-image tests passed. The single full `python -m pytest backend/tests` run under the isolated Python 3.11 environment finished with **1,718 passed, 11 skipped** in 390.51 seconds. `git diff --check` passed. A final focused Visuals/provider run after a small button-state correction passed with 11 tests.

| Case | Result | Peak allocated VRAM | Inference time | Output SHA-256 |
| --- | --- | ---: | ---: | --- |
| GTX 1660 SUPER, 512×512 → 1024×1024 | Passed | 21,956,096 bytes | 0.591 s | `eb158d4fb9872f299816beba46a4acbbe4b641a632cbefd7b91c8ededef497db` |
| GTX 1660 SUPER, 512×512 → 2048×2048 | Passed | 21,956,096 bytes | 0.501 s | `7b77c8c2c0de0f5702a8fbc5969cebb37647d7d103580faa55def10594da0ad1` |
| GTX 1660 SUPER, 960×540 → 3840×2160 | Passed | 21,956,096 bytes | 0.749 s | `b7a02f7572952c82a087f3a2a71e6eef583d37d85fb3325092b2bc730bdf3306` |

Measured GPU: NVIDIA GeForce GTX 1660 SUPER, driver 595.71, reported 6144 MiB. Peak VRAM above is PyTorch peak allocated device memory during inference, not whole-device process occupancy. Explicit `python -m app.runtime.upscale_runtime verify` passed after all three real inferences with fingerprint `a7c551a6bcea22fe3831c2d3f9157028a9e63100545cc00c6977def34fb85322`. Limitations: first-use inventory verification reads the full private runtime; inferred 2x is a final resize of a native x4 result; the Visuals check ran offscreen, so interactive on-screen review and render playback remain open.

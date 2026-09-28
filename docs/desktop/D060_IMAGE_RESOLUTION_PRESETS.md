# D060 orientation and final-resolution presets

## User choices and dimensions

| Orientation | SD source dimensions | Draft / Source | Full HD | QHD / 1440p | 4K UHD |
| --- | ---: | ---: | ---: | ---: | ---: |
| Landscape (16:9) | 640×360 | 640×360 | 1920×1080 | 2560×1440 | 3840×2160 |
| Portrait (9:16) | 360×640 | 360×640 | 1080×1920 | 1440×2560 | 2160×3840 |

Final-resolution selection is absent from the Stable Diffusion request and fingerprint. Source generation is keyed by the existing prompt, orientation dimensions and seed. A selected resolution change can therefore reuse the retained generated source. Draft selects that source directly. Other choices run one Real-ESRGAN native x4 inference and resize the result with Pillow Lanczos only when native x4 dimensions differ from the exact target. No chained neural passes or crop/stretch path is used.

FHD from a 640×360 source is x4 2560×1440 followed by Lanczos downsample. QHD is native x4 2560×1440 without resize. 4K UHD is native x4 2560×1440 followed by Lanczos enlargement to 3840×2160. Portrait follows the corresponding dimensions. The final lineage is schema version 2 and records source identity/checksum, profile, target and native dimensions, provider/runtime identity and diagnostics. Existing version-1 D059 factor derivatives keep their existing `scale=2/4` meaning and load unchanged.

## Runtime profiles and upgrade

D057/D060 profile: `sd15-cu124-fp32-inference-v3-phased-diagnostics`. It supports exactly 512×512, 640×360 and 360×640; dimensions must be multiples of eight. The checkpoint, Python package pins, FP32, DDIM, steps, guidance, safety checker and attention slicing remain the same. The worker protocol remains version 2; the v3 profile and worker inventory reject the older private installation.

D059 profile: `realesr-general-x4v3-cu124-fp32-tile128-v2-exact-target`. Its worker request carries target width/height and performs a single native x4 inference. The official model and SHA-256 remain as recorded in [D059 evidence](D059_LOCAL_IMAGE_UPSCALING.md). The old v1 managed worker is rejected by verification.

For the existing Windows development setup, build new immutable roots from the already prepared sources (no package/model download occurs in these commands):

```powershell
$generation = (Get-Content 'D:\AI Content Studio\local-image-d060\active.json' | ConvertFrom-Json).generation
$source = "D:\AI Content Studio\local-image-d060\installed\$generation"
python -m app.runtime.local_image_runtime install 'D:\AI Content Studio\local-image-d060-v3' "$source\runtime" "$source\model"
python -m app.runtime.local_image_runtime verify 'D:\AI Content Studio\local-image-d060-v3'
$upscaleGeneration = (Get-Content 'D:\AI Content Studio\upscaler-d060\active.json' | ConvertFrom-Json).generation
$upscaleSource = "D:\AI Content Studio\upscaler-d060\installed\$upscaleGeneration"
python -m app.runtime.upscale_runtime install 'D:\AI Content Studio\upscaler-d060-v2' "$upscaleSource\runtime" "$upscaleSource\model.pth"
python -m app.runtime.upscale_runtime verify 'D:\AI Content Studio\upscaler-d060-v2'
```

Set `AICS_LOCAL_IMAGE_ROOT` and `AICS_LOCAL_UPSCALE_ROOT` to those new roots. The upscaler is rebuilt from its own existing private runtime and pinned `model.pth`, never from the Stable Diffusion runtime. Old managed generations remain immutable; the application does not silently migrate them.

The SD provider timeout remains bounded at 600 seconds (`LOCAL_IMAGE_TIMEOUT_SECONDS`). The worker now emits bounded `local_image_phase:` records for startup, imports, model load and CUDA transfer, inference, safety/output validation, and PNG encoding. Each completed phase reports elapsed seconds; final diagnostics include inference time, worker time, peak allocated VRAM, dtype, device, safety result, black-output result and encoded byte count. A timeout reports the last structured phase received from stderr without displaying raw worker logs.

## Evidence

Default automated tests remain offline. Focused mapping, worker/provider bounds, exact target lineage, cache, import aspect validation, GUI and legacy artifact tests cover orientation and request dimensions.

The misleading square preview was caused by `_show()` always rendering the persisted selected artifact while orientation changes updated only the size labels. Orientation changes now compare decoded preview dimensions with the selected preset and replace an incompatible bitmap with a neutral message. The selection and history remain untouched. Successful generated variants update selection and the preview immediately.

The earlier v2 timeout cannot be diagnosed retrospectively because that worker emitted no phase telemetry. It was not reproduced on the v3 worker: both controlled runs showed active CUDA work and completed well inside the existing 600-second bound. No timeout increase was justified. The v3 worker reports structured phase start/completion records and per-phase durations; timeout errors include the last phase observed without exposing raw logs.

### Direct SD provider runs

Both runs used the exact GPS prompt specified for reproduction, FP32 on CUDA 0, 20 DDIM steps, guidance 7.5, attention slicing, the GTX 1660 SUPER / driver 595.71, and the verified v3 installation fingerprint `f8c2b43f5a4fc345edbeead6951d68b44413b3bf960a2a902a4dee9cb6f854f9`. Provider time includes worker startup and inference; inventory verification was completed before timing.

| Case / seed | Completed / decoded output | Provider time | Imports | Model load + CUDA | Inference | Peak allocated VRAM | Dtype / device | Safety / black | SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| Landscape / 60 | Yes / 640×360 | 253.506 s | 11.721 s | 27.938 s | 212.283 s | 6,227,755,520 B (5.80 GiB) | FP32 / cuda:0, NVIDIA GeForce GTX 1660 SUPER | false / false | `1dbf55f65d6869e0345413160928cd8d307b8cc9a61475bbf854270596ca256e` |
| Portrait / 61 | Yes / 360×640 | 195.979 s | 5.283 s | 9.873 s | 179.678 s | 6,227,755,520 B (5.80 GiB) | FP32 / cuda:0, NVIDIA GeForce GTX 1660 SUPER | false / false | `b33c5e62c18dfa622a69c04b285ab80eb037f23889e4a6b940af677936a7b39a` |

### `test-gps-en-2` Visuals panel acceptance

Ran the real Qt `ScenePanel` with the managed providers and persisted project services in offscreen mode. On opening, the legacy selected 2048×2048 variant remained selected and in history; the preview showed `Selected image: 2048 × 2048 (1:1)` and `Not compatible with Landscape (16:9)`. Switching to Portrait reported that the selected 640×360 source was incompatible without changing its selection. The UI-thread preview pixmaps measured 320×180 for Landscape and 101×180 for Portrait.

| Action | Result |
| --- | --- |
| Landscape, Draft / Source, seed 60 | Selected `artifact_25cc542b330d408dae46179d6ab85982`, decoded 640×360; dropdown includes `generated: generated-image.png (640×360)`; 191.094 s GUI wait. No upscaler was invoked. |
| Portrait, Draft / Source, seed 61 | Selected `artifact_e7bd7e848f954d17af396c16b1bfe89a`, decoded 360×640; dropdown includes `generated: generated-image.png (360×640)`; 196.531 s GUI wait. |
| Landscape Full HD | Selected `artifact_f8a503d791654e2bb5d2dbcfc8e79a38`, decoded 1920×1080, source is the retained Landscape artifact above. |
| Landscape QHD | Decoded 2560×1440, native x4 2560×1440, no resize; 5.797 s. |
| Landscape 4K UHD | Decoded 3840×2160, native x4 2560×1440, Lanczos resize; 7.125 s. |
| Portrait Full HD | Decoded 1080×1920, native x4 1440×2560, Lanczos resize; 6.375 s. |

After reopen, the selected Landscape FHD derivative and its Landscape source lineage remained intact. `compose_timeline(...).media.resolve(...)` resolved the scene to the same selected final artifact at 1920×1080. Legacy imported, square generated and D059 square-upscaled variants remained in scene history. The widget run was offscreen; the native on-screen font/rendering presentation was not manually reviewed on this machine.

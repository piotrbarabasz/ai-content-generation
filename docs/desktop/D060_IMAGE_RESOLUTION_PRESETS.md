# D060 orientation and final-resolution presets

## User choices and dimensions

| Orientation | SD source dimensions | Draft / Source | Full HD | QHD / 1440p | 4K UHD |
| --- | ---: | ---: | ---: | ---: | ---: |
| Landscape (16:9) | 640×360 | 640×360 | 1920×1080 | 2560×1440 | 3840×2160 |
| Portrait (9:16) | 360×640 | 360×640 | 1080×1920 | 1440×2560 | 2160×3840 |

Final-resolution selection is absent from the Stable Diffusion request and fingerprint. Source generation is keyed by the existing prompt, orientation dimensions and seed. A selected resolution change can therefore reuse the retained generated source. Draft selects that source directly. Other choices run one Real-ESRGAN native x4 inference and resize the result with Pillow Lanczos only when native x4 dimensions differ from the exact target. No chained neural passes or crop/stretch path is used.

FHD from a 640×360 source is x4 2560×1440 followed by Lanczos downsample. QHD is native x4 2560×1440 without resize. 4K UHD is native x4 2560×1440 followed by Lanczos enlargement to 3840×2160. Portrait follows the corresponding dimensions. The final lineage is schema version 2 and records source identity/checksum, profile, target and native dimensions, provider/runtime identity and diagnostics. Existing version-1 D059 factor derivatives keep their existing `scale=2/4` meaning and load unchanged.

## Runtime profiles and upgrade

D057 profile: `sd15-cu124-fp32-inference-v2-aspect-sizes`. It supports exactly 512×512, 640×360 and 360×640; dimensions must be multiples of eight. The checkpoint, Python package pins, FP32, DDIM, steps, guidance, safety checker and attention slicing remain the same. The worker protocol is version 2; an older private installation fails profile/worker verification.

D059 profile: `realesr-general-x4v3-cu124-fp32-tile128-v2-exact-target`. Its worker request carries target width/height and performs a single native x4 inference. The official model and SHA-256 remain as recorded in [D059 evidence](D059_LOCAL_IMAGE_UPSCALING.md). The old v1 managed worker is rejected by verification.

For the existing Windows development setup, build new immutable roots from the already prepared sources (no package/model download occurs in these commands):

```powershell
$generation = (Get-Content 'D:\AI Content Studio\local-image\active.json' | ConvertFrom-Json).generation
$source = "D:\AI Content Studio\local-image\installed\$generation"
python -m app.runtime.local_image_runtime install 'D:\AI Content Studio\local-image-d060' "$source\runtime" "$source\model"
python -m app.runtime.local_image_runtime verify 'D:\AI Content Studio\local-image-d060'
python -m app.runtime.upscale_runtime install 'D:\AI Content Studio\upscaler-d060' "$source\runtime" 'D:\AI Content Studio\realesr-general-x4v3.pth'
python -m app.runtime.upscale_runtime verify 'D:\AI Content Studio\upscaler-d060'
```

Set `AICS_LOCAL_IMAGE_ROOT` and `AICS_LOCAL_UPSCALE_ROOT` to those new roots. Old managed generations remain immutable and inactive; the application does not silently migrate them.

## Evidence

Default automated tests are offline. Focused mapping, worker/provider bounds, exact target lineage, cache, import aspect validation, GUI and legacy artifact tests run before hardware acceptance.

| GTX 1660 SUPER / 595.71 | Output | Inference | Peak VRAM | SHA-256 | Status |
| --- | ---: | ---: | ---: | --- | --- |
| SD Landscape source 640×360 | — | — | — | — | Pending |
| SD Portrait source 360×640 | — | — | — | — | Pending |
| FHD Landscape from retained source | 1920×1080 | — | — | — | Pending |
| QHD Landscape from same source | 2560×1440 | — | — | — | Pending |
| 4K UHD Landscape from same source | 3840×2160 | — | — | — | Pending |
| FHD Portrait from retained source | 1080×1920 | — | — | — | Pending |

Timeline/reopen acceptance on `test-gps-en-2` is pending. D060 remains Partial until both orientation generation runs, final-resolution reuse, exact output validation, interactive GUI and Timeline selection pass on the target GPU.

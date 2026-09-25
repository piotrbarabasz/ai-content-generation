# D057 local image runtime — partial implementation evidence

The first profile is `sd15-cu124-fp32-inference-v1`: [Stable Diffusion v1.5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/tree/f03de327dd89b501a01da37fc5240cf4fdba85a1) at commit `f03de327dd89b501a01da37fc5240cf4fdba85a1`. The model declares the CreativeML OpenRAIL-M license; model use and any redistribution must follow that license. Only the fifteen required Diffusers files are copied, with their upstream SHA-256 values pinned in `local_image_runtime.py`. The model is never kept in a project workspace or the application source tree.

This is a CUDA-only, Python 3.11.9, 512×512 PNG profile. It keeps the pinned fp16 checkpoint files but loads them for float32 inference with attention slicing; fp16 inference on the target GTX 1660 SUPER produced entirely non-finite latents. It uses PyTorch 2.6.0+cu124, torchvision 0.21.0+cu124, Diffusers 0.35.1, Transformers 4.49.0, Accelerate 1.4.0, Safetensors 0.5.3, Hugging Face Hub 0.34.4 and Pillow 11.1.0. PyTorch lists the [2.6.0 CUDA 12.4 wheel channel](https://docs.pytorch.org/get-started/previous-versions/). The worker uses DDIM, 20 steps, guidance 7.5, seed on `cuda:0`, optional negative prompt, and the model's safety checker. These settings and the complete installed file inventory enter the provider identity. CUDA seeds identify repeatable requests, but cross-driver/architecture bitwise determinism is not claimed. Approximately 6 GiB reported CUDA VRAM is the preflight target, not a memory guarantee. The preflight permits a bounded 1 MiB reporting tolerance: its previous exact-byte check incorrectly rejected a GTX 1660 SUPER reporting 6143.6875 MiB (0.3125 MiB short of exactly 6 GiB). Meaningfully smaller GPUs remain rejected.

## Explicit offline intake

Prepare a private, self-contained Windows x64 CPython 3.11.9 embeddable directory outside the project. Its `python311._pth` must contain exactly `python311.zip`, `.`, and `packages` on separate lines. Populate `packages` from an explicitly reviewed local wheelhouse with the package pins above and their required transitive wheels. The intake command itself performs no network access and rejects wrong Python or critical package versions. A future release-grade distribution manifest still needs a reviewed hash/license pin for every transitive wheel before this profile can be called completed.

Download the selected Hugging Face revision explicitly into a separate local snapshot directory. Installation copies only the pinned file list and verifies its upstream SHA-256 checksums. Do not use a floating `main` snapshot.

With `PYTHONPATH=backend`, run:

```powershell
python -m app.runtime.local_image_runtime install "$env:LOCALAPPDATA\AI Content Studio\local-image" <private-python-directory> <pinned-model-snapshot-directory>
python -m app.runtime.local_image_runtime verify "$env:LOCALAPPDATA\AI Content Studio\local-image"
$env:AICS_IMAGE_PROVIDER = "local"
$env:AICS_LOCAL_IMAGE_ROOT = "$env:LOCALAPPDATA\AI Content Studio\local-image"
```

Intake copies into a generation directory, checks the interpreter, package versions and model hashes, records all private runtime files, and publishes a pointer only after verification. Restart discovery rehashes both inventories. Failed candidates remain inactive. The worker's Hugging Face, Torch, CUDA, Triton, Numba, compiler and temp caches are directed to `<root>/cache`, outside both verified inventories. Inference uses offline environment flags and a trusted model path supplied by composition, never a job-supplied filesystem path.

## Worker and GPU behavior

`LocalImageProvider` acquires `APPLICATION_GPU_RESOURCES` before launching a one-shot private Python process. The worker receives a versioned JSON request with prompt, negative prompt, size, seed and format; it returns PNG bytes. The provider validates single-frame, fully decoded, bounded 512×512 PNG output before `ImageGenerationService` publishes it. Its process is reaped before the D028 lease is released; uncertain cleanup quarantines ownership. The Visuals panel runs inference in a Qt worker thread and returns publication/selection to the project coordinator thread. Image generation does not create another repository session.

The worker reports `nsfw_content_detected`, effective-black detection, dtype, device and elapsed inference time. Safety-checker blocks and effectively black results now fail explicitly before publication; the safety checker remains enabled and there is no bypass. Successful provider metadata retains these diagnostics. Float32 inference may take close to five minutes on the GTX 1660 SUPER, so the worker timeout is 600 seconds.

The existing `ImageGenerationService` owns request fingerprints, jobs, caching, publication and stale-result checks. OpenAI Images and imported images remain separate paths. An unavailable local runtime reports an actionable error and cannot activate itself or download weights.

The worker is copied into and fingerprinted with each immutable managed installation. After a worker change, move/remove the old managed installation and install a new one from the unchanged private runtime and model sources; do not patch installed worker bytes in place.

## Required real acceptance still open

On a Windows NVIDIA machine, create the private runtime and pinned snapshot; record the exact wheelhouse inventory and licenses. Verify installation, then run the application with `AICS_IMAGE_PROVIDER=local` and a scene that already has a selected visual prompt. Generate the fixed prompt “A small GPS satellite orbiting Earth, educational cinematic illustration, dark space background, no text.” with seed 43 at 512×512. Record GPU name, driver, package versions, elapsed time, peak VRAM, output PNG measurements, retained artifact/selection and timeline use. Repeat with another seed and verify both variants remain. Cancel a run and test OOM handling without changing the prior selection. Close the application, verify D028 ownership is released, restart, rerun `verify`, and generate again. Observe that the Qt UI stays responsive.

The GTX 1660 SUPER reports 6143.6875 MiB to PyTorch. A direct fp16 diagnostic with the fixed prompt/seed found 0% finite latents and VAE pixels; Diffusers then reported `nsfw_content_detected=[True]` and returned black pixels. The safety flag was downstream of invalid inference, not evidence that the prompt was unsafe. Loading the same pinned checkpoint for float32 inference with attention slicing produced `nsfw_content_detected=[False]` and non-black channel ranges, taking 269.39 seconds with 6030.3 MiB peak PyTorch-allocated GPU memory.

After a fresh managed install, the real `LocalImageProvider` smoke with the fixed prompt and seed 43 succeeded and saved a 512×512 PNG outside the repository. Its RGB channel extrema were `(19–255, 13–255, 14–255)` with 107675 distinct RGB colors; the image was visually inspected. Returned diagnostics were `nsfw_content_detected=False`, `effectively_black=False`, `dtype=torch.float32`, `device=cuda:0`, and 221.694 seconds for inference. The end-to-end provider call took 429.08 seconds including runtime verification and worker startup. A separate managed-runtime verification after inference passed with the same fingerprint. The original installation was preserved as a backup rather than edited in place. The reviewed transitive-wheel manifest, project GUI/selection/timeline smoke, another seed, cancellation and OOM acceptance remain open; D057 stays Partial.

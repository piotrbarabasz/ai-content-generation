# D057 local image runtime — partial implementation evidence

The first profile is `sd15-cu124-fp16-v1`: [Stable Diffusion v1.5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/tree/f03de327dd89b501a01da37fc5240cf4fdba85a1) at commit `f03de327dd89b501a01da37fc5240cf4fdba85a1`. The model declares the CreativeML OpenRAIL-M license; model use and any redistribution must follow that license. Only the fifteen required Diffusers files are copied, with their upstream SHA-256 values pinned in `local_image_runtime.py`. The model is never kept in a project workspace or the application source tree.

This is a CUDA-only, Python 3.11.9, 512×512 PNG profile. It uses fp16 weights, PyTorch 2.6.0+cu124, torchvision 0.21.0+cu124, Diffusers 0.35.1, Transformers 4.49.0, Accelerate 1.4.0, Safetensors 0.5.3, Hugging Face Hub 0.34.4 and Pillow 11.1.0. PyTorch lists the [2.6.0 CUDA 12.4 wheel channel](https://docs.pytorch.org/get-started/previous-versions/). The worker uses DDIM, 20 steps, guidance 7.5, seed on `cuda:0`, optional negative prompt, and the model's safety checker. These settings and the complete installed file inventory enter the provider identity. CUDA seeds identify repeatable requests, but cross-driver/architecture bitwise determinism is not claimed. Six GiB VRAM is the target, not a measured guarantee.

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

The existing `ImageGenerationService` owns request fingerprints, jobs, caching, publication and stale-result checks. OpenAI Images and imported images remain separate paths. An unavailable local runtime reports an actionable error and cannot activate itself or download weights.

## Required real acceptance still open

On a Windows NVIDIA machine, create the private runtime and pinned snapshot; record the exact wheelhouse inventory and licenses. Verify installation, then run the application with `AICS_IMAGE_PROVIDER=local` and a scene that already has a selected visual prompt. Generate the fixed prompt “A small GPS satellite orbiting Earth, educational cinematic illustration, dark space background, no text.” with seed 43 at 512×512. Record GPU name, driver, package versions, elapsed time, peak VRAM, output PNG measurements, retained artifact/selection and timeline use. Repeat with another seed and verify both variants remain. Cancel a run and test OOM handling without changing the prior selection. Close the application, verify D028 ownership is released, restart, rerun `verify`, and generate again. Observe that the Qt UI stays responsive.

The available GTX 1660 SUPER has 6144 MiB VRAM and driver 595.71, but the pinned model assets and private image wheelhouse were not installed for this change. No real image, generation time, peak VRAM or post-real-inference restart measurement is claimed. First visual prompt creation still needs explicit brief/style context; Film Brief and Visual Style UI are outside D057.

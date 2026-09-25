"""One-shot private SD 1.5 inference worker; stdin is a versioned data request."""

import io
import json
import os
from pathlib import Path
import sys


MIN_CUDA_VRAM_BYTES = 6 * 1024**3
CUDA_VRAM_REPORTING_TOLERANCE_BYTES = 1 * 1024**2


def _has_sufficient_cuda_vram(reported_bytes):
    return reported_bytes >= MIN_CUDA_VRAM_BYTES - CUDA_VRAM_REPORTING_TOLERANCE_BYTES


def _require_cuda_device(cuda):
    if not cuda.is_available() or cuda.device_count() < 1:
        raise RuntimeError("CUDA device 0 is unavailable; no CPU fallback is permitted.")
    if not _has_sufficient_cuda_vram(cuda.get_device_properties(0).total_memory):
        raise RuntimeError("Local image profile requires approximately 6 GiB reported CUDA VRAM.")


def main():
    binary_output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    request = json.loads(sys.stdin.buffer.readline(16384))
    if (set(request) != {"version", "prompt", "negative_prompt", "width", "height", "seed", "format"}
            or request["version"] != 1 or request["format"] != "PNG"
            or (request["width"], request["height"]) != (512, 512)
            or type(request["seed"]) is not int or not 0 <= request["seed"] < 2**32
            or type(request["prompt"]) is not str or not request["prompt"].strip()
            or type(request["negative_prompt"]) is not str):
        raise ValueError("Invalid local image worker request.")
    model = Path(os.environ["AICS_LOCAL_IMAGE_MODEL"]).resolve(strict=True)
    import torch
    from diffusers import StableDiffusionPipeline, DDIMScheduler

    _require_cuda_device(torch.cuda)
    pipe = StableDiffusionPipeline.from_pretrained(
        str(model), local_files_only=True, torch_dtype=torch.float16,
        variant="fp16", use_safetensors=True,
    )
    pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config)
    pipe.to("cuda:0")
    generator = torch.Generator(device="cuda:0").manual_seed(request["seed"])
    image = pipe(
        prompt=request["prompt"], negative_prompt=request["negative_prompt"],
        width=512, height=512, num_inference_steps=20, guidance_scale=7.5,
        generator=generator,
    ).images[0]
    output = io.BytesIO()
    image.save(output, format="PNG")
    binary_output.write(output.getvalue())
    binary_output.close()


if __name__ == "__main__":
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", DIFFUSERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1")
    sys.dont_write_bytecode = True
    try:
        main()
    except Exception as exc:
        import traceback
        if "out of memory" in str(exc).lower():
            print("gpu_oom: CUDA memory exhausted.", file=sys.stderr)
        else:
            print(f"local_image_error: {type(exc).__name__}: {str(exc)[:400]}", file=sys.stderr)
        raise SystemExit(1)

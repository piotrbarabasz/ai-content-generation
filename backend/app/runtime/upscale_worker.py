"""One-shot Real-ESRGAN SRVGG inference. Architecture adapted from
xinntao/Real-ESRGAN v0.3.0, BSD-3-Clause; see REALESRGAN_LICENSE.txt.
"""

import base64
import io
import json
import os
import sys
from time import perf_counter


def main():
    binary = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    request = json.loads(sys.stdin.buffer.readline(64 * 1024 * 1024 + 4096))
    if set(request) != {"version", "image", "factor"} or request["version"] != 1 or request["factor"] not in (2, 4):
        raise ValueError("Invalid upscale worker request.")
    from PIL import Image
    import numpy as np
    import torch
    from torch import nn
    from torch.nn import functional as F

    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise RuntimeError("CUDA device 0 is unavailable; CPU fallback is disabled.")

    class SRVGGNetCompact(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.ModuleList()
            self.body.append(nn.Conv2d(3, 64, 3, 1, 1))
            self.body.append(nn.PReLU(num_parameters=64))
            for _ in range(32):
                self.body.append(nn.Conv2d(64, 64, 3, 1, 1))
                self.body.append(nn.PReLU(num_parameters=64))
            self.body.append(nn.Conv2d(64, 48, 3, 1, 1))
            self.upsampler = nn.PixelShuffle(4)

        def forward(self, x):
            out = x
            for layer in self.body:
                out = layer(out)
            return self.upsampler(out) + F.interpolate(x, scale_factor=4, mode="nearest")

    model = SRVGGNetCompact()
    weights = torch.load(os.environ["AICS_UPSCALE_MODEL"], map_location="cpu", weights_only=True)
    model.load_state_dict(weights.get("params_ema", weights.get("params", weights)), strict=True)
    model.eval().float().to("cuda:0")
    image = Image.open(io.BytesIO(base64.b64decode(request["image"], validate=True)))
    image.load()
    image = image.convert("RGB")
    width, height = image.size
    source = np.asarray(image, dtype=np.float32) / 255.0
    output = np.empty((height * 4, width * 4, 3), dtype=np.uint8)
    torch.cuda.reset_peak_memory_stats(0)
    start = perf_counter()
    with torch.inference_mode():
        for y in range(0, height, 128):
            for x in range(0, width, 128):
                x0, y0 = max(0, x - 10), max(0, y - 10)
                x1, y1 = min(width, x + 128 + 10), min(height, y + 128 + 10)
                tile = torch.from_numpy(source[y0:y1, x0:x1].transpose(2, 0, 1).copy()).unsqueeze(0).to("cuda:0")
                pred = model(tile).clamp_(0, 1)
                left, top = (x - x0) * 4, (y - y0) * 4
                right, bottom = left + min(128, width - x) * 4, top + min(128, height - y) * 4
                patch = (pred[0, :, top:bottom, left:right].permute(1, 2, 0).mul(255).round()
                         .byte().cpu().numpy())
                output[y * 4:y * 4 + patch.shape[0], x * 4:x * 4 + patch.shape[1]] = patch
                del tile, pred
    if request["factor"] == 2:
        output = np.asarray(Image.fromarray(output, "RGB").resize((width * 2, height * 2), Image.Resampling.LANCZOS))
    elapsed = perf_counter() - start
    diagnostics = {"device": torch.cuda.get_device_name(0), "dtype": "float32", "tile": 128,
                   "tile_pad": 10, "pre_pad": 0, "elapsed_inference_seconds": round(elapsed, 3),
                   "peak_vram_bytes": torch.cuda.max_memory_allocated(0),
                   "final_resize": "Pillow Lanczos after native x4" if request["factor"] == 2 else None}
    print("upscale_diagnostics:" + json.dumps(diagnostics, sort_keys=True), file=sys.stderr, flush=True)
    encoded = io.BytesIO()
    Image.fromarray(output, "RGB").save(encoded, format="PNG")
    binary.write(encoded.getvalue())
    binary.close()


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    try:
        main()
    except Exception as exc:
        if "out of memory" in str(exc).lower():
            print("gpu_oom: CUDA memory exhausted.", file=sys.stderr)
        else:
            print(f"upscale_error: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        raise SystemExit(1)

"""One optional CUDA image provider through a verified private worker."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
from threading import Lock

from PIL import Image

from app.providers.image_generation import ImageGenerationCapabilities, ImageGenerationRequest, ImageGenerationResult
from app.runtime.local_image_runtime import LocalImageInstallation, MODEL, MODEL_REVISION, PROFILE, _python
from app.runtime.resources import APPLICATION_GPU_RESOURCES
from app.storage.image_decoder import ImageLimits, decode_image


MAX_IMAGE_BYTES = 16 * 1024 * 1024


def _worker_diagnostics(errors):
    for line in reversed(errors.decode("utf-8", errors="replace").splitlines()):
        if line.startswith("local_image_diagnostics:"):
            try:
                value = json.loads(line.removeprefix("local_image_diagnostics:"))
            except ValueError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _effectively_black_png(data):
    with Image.open(BytesIO(data)) as image:
        return max(high for _, high in image.convert("RGB").getextrema()) <= 2


def _diagnostic_summary(diagnostics):
    if not diagnostics:
        return ""
    return (" (nsfw_content_detected={nsfw_content_detected}, effectively_black={effectively_black}, "
            "dtype={dtype}, device={device}, elapsed_inference_seconds={elapsed_inference_seconds})"
            .format(**{key: diagnostics.get(key) for key in (
                "nsfw_content_detected", "effectively_black", "dtype", "device", "elapsed_inference_seconds")}))


class LocalImageProvider:
    requires_background = True

    def __init__(self, root, *, resources=None, process_factory=None):
        self.installation = LocalImageInstallation(root)
        self.resources = APPLICATION_GPU_RESOURCES if resources is None else resources
        self.process_factory = subprocess.Popen if process_factory is None else process_factory
        self._lock = Lock()
        self._process = None
        self._cancel_requested = False

    def cancel(self):
        with self._lock:
            self._cancel_requested = True
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def _installed(self):
        installed = self.installation.active()
        if installed is None:
            raise ValueError("Install the pinned local image runtime and model explicitly first.")
        return installed

    def capabilities(self):
        installed = self._installed()
        return ImageGenerationCapabilities(
            "local", MODEL + "@" + MODEL_REVISION, PROFILE, formats=("PNG",),
            max_dimension=512, max_pixels=512 * 512, negative_prompt=True, seeded=True,
            supported_sizes=((512, 512),),
            settings={"installation": installed.fingerprint, "device": "cuda:0", "dtype": "float32",
                      "attention_slicing": True, "scheduler": "DDIM", "steps": 20, "guidance_scale": 7.5},
        )

    @staticmethod
    def _environment(installed):
        cache = installed.cache.resolve()
        if cache == installed.runtime or installed.runtime in cache.parents:
            raise ValueError("Mutable inference cache must be outside the verified runtime.")
        folders = {
            "HF_HOME": cache / "hf", "HF_HUB_CACHE": cache / "hf" / "hub",
            "TRANSFORMERS_CACHE": cache / "transformers", "TORCH_HOME": cache / "torch",
            "XDG_CACHE_HOME": cache / "xdg", "TRITON_CACHE_DIR": cache / "triton",
            "CUDA_CACHE_PATH": cache / "cuda", "TORCHINDUCTOR_CACHE_DIR": cache / "inductor",
            "NUMBA_CACHE_DIR": cache / "numba", "MPLCONFIGDIR": cache / "matplotlib",
            "TEMP": cache / "temp", "TMP": cache / "temp",
        }
        for path in folders.values():
            path.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("PYTHON") and key.upper() != "VIRTUAL_ENV"}
        env.update({key: str(path) for key, path in folders.items()})
        if os.name == "nt":
            from app.runtime.native_piper import system_directory
            env["PATH"] = str(installed.runtime) + os.pathsep + str(system_directory())
        env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                   TRANSFORMERS_OFFLINE="1", DIFFUSERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                   AICS_LOCAL_IMAGE_MODEL=str(installed.model))
        return env

    def generate(self, request: ImageGenerationRequest):
        self.capabilities().validate(request)
        installed = self._installed()
        lease = self.resources.acquire("local-image", "cuda:0")
        if lease is None:
            raise RuntimeError("GPU is occupied by another managed workload; retry after it finishes.")
        process = job = None
        try:
            with self._lock:
                self._cancel_requested = False
            payload = request.to_payload() | {"version": 1}
            from app.domain.dependencies import canonical_json
            process = self.process_factory(
                [str(_python(installed.runtime)), "-I", "-B", "-u",
                 str(installed.runtime / "local_image_worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=installed.runtime, env=self._environment(installed),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            with self._lock:
                self._process = process
                if self._cancel_requested and process.poll() is None:
                    process.kill()
            self.resources.attach(lease, process)
            if os.name == "nt":
                from app.runtime.windows_job import WindowsJob
                job = WindowsJob(process.pid)
            try:
                output, errors = process.communicate((canonical_json(payload) + "\n").encode(), timeout=600)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Local image generation timed out.") from None
            if process.returncode != 0:
                if self._cancel_requested:
                    raise RuntimeError("Local image generation canceled.")
                error = errors.decode("utf-8", errors="replace")[-800:]
                summary = _diagnostic_summary(_worker_diagnostics(errors))
                if "gpu_oom:" in error:
                    raise RuntimeError("Local image CUDA memory exhausted; previous image remains selected.")
                if "local_image_safety_blocked:" in error:
                    raise RuntimeError("Local image safety checker blocked the result; try another prompt or seed."
                                       + summary)
                if "local_image_black_output:" in error:
                    raise RuntimeError("Local image inference produced an effectively black result; no image was published."
                                       + summary)
                raise RuntimeError("Local image worker failed; inspect the installed runtime and model.")
            measured = decode_image(output, ImageLimits(max_bytes=MAX_IMAGE_BYTES,
                                                        max_dimension=512, max_pixels=512 * 512))
            if (measured["format"], measured["width"], measured["height"]) != ("PNG", 512, 512):
                raise ValueError("Local image output differs from the requested PNG dimensions.")
            if _effectively_black_png(output):
                raise ValueError("Local image worker returned an effectively black PNG; no image was published.")
            return ImageGenerationResult(output, "PNG", 512, 512,
                                         {"sha256": sha256(output).hexdigest(), "runtime": installed.fingerprint,
                                          "diagnostics": _worker_diagnostics(errors)})
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.resources.quarantine(lease)
                    raise RuntimeError("Local image worker cleanup is uncertain; GPU ownership is quarantined.")
            if job is not None:
                job.close()
            if not self.resources.availability().quarantined:
                self.resources.release(lease)
            with self._lock:
                self._process = None


__all__ = ["LocalImageProvider"]

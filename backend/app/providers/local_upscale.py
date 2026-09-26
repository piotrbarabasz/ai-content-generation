"""Optional Real-ESRGAN CUDA provider with D028 ownership and process cleanup."""

import base64
from hashlib import sha256
import json
import os
import subprocess
from threading import Lock

from app.providers.image_upscale import ImageUpscaleCapabilities, ImageUpscaleRequest, ImageUpscaleResult
from app.runtime.resources import APPLICATION_GPU_RESOURCES
from app.runtime.upscale_runtime import LocalUpscaleInstallation, MODEL, MODEL_SHA256, PROFILE, _python
from app.storage.image_decoder import decode_image
from app.application.image_upscale import OUTPUT_LIMITS


class LocalUpscaleProvider:
    requires_background = True

    def __init__(self, root, *, resources=None, process_factory=None):
        self.installation = LocalUpscaleInstallation(root)
        self.resources = APPLICATION_GPU_RESOURCES if resources is None else resources
        self.process_factory = subprocess.Popen if process_factory is None else process_factory
        self._lock = Lock()
        self._process = None
        self._cancel_requested = False
        self._verified = None

    def cancel(self):
        with self._lock:
            self._cancel_requested = True
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def reset_cancel(self):
        with self._lock:
            if self._process is not None:
                raise RuntimeError("Previous upscaler worker is still active.")
            self._cancel_requested = False

    def _installed(self):
        installed = self._verified
        if installed is None:
            installed = self.installation.active()
        if installed is None:
            raise ValueError("Install the pinned upscaler runtime and model explicitly first.")
        self._verified = installed
        return installed

    def capabilities(self):
        installed = self._installed()
        return ImageUpscaleCapabilities("local", MODEL + "@" + MODEL_SHA256, PROFILE, installed.fingerprint)

    @staticmethod
    def _environment(installed):
        cache = installed.cache.resolve()
        if cache == installed.runtime or installed.runtime in cache.parents:
            raise ValueError("Mutable cache must be outside the verified upscaler runtime.")
        folders = {"TEMP": cache / "temp", "TMP": cache / "temp", "TORCH_HOME": cache / "torch",
                   "CUDA_CACHE_PATH": cache / "cuda", "XDG_CACHE_HOME": cache / "xdg"}
        for path in folders.values():
            path.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("PYTHON") and key.upper() != "VIRTUAL_ENV"}
        env.update({key: str(path) for key, path in folders.items()})
        if os.name == "nt":
            from app.runtime.native_piper import system_directory
            env["PATH"] = str(installed.runtime) + os.pathsep + str(system_directory())
        env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                   AICS_UPSCALE_MODEL=str(installed.model))
        return env

    def upscale(self, request: ImageUpscaleRequest):
        self.capabilities().validate(request)
        if len(request.image_bytes) > 47 * 1024 * 1024:
            raise ValueError("Upscale source exceeds the bounded worker input size.")
        measured = decode_image(request.image_bytes, OUTPUT_LIMITS)
        if (measured["format"], measured["width"], measured["height"]) != (request.format, request.width, request.height):
            raise ValueError("Decoded source differs from upscale request.")
        installed = self._installed()
        lease = self.resources.acquire("local-upscale", "cuda:0")
        if lease is None:
            raise RuntimeError("GPU is occupied by another managed workload; retry after it finishes.")
        process = job = None
        try:
            payload = json.dumps({"version": 1, "image": base64.b64encode(request.image_bytes).decode("ascii"),
                                  "factor": request.factor}, separators=(",", ":")).encode() + b"\n"
            process = self.process_factory(
                [str(_python(installed.runtime)), "-I", "-B", "-u", str(installed.runtime / "upscale_worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=installed.runtime, env=self._environment(installed),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            with self._lock:
                self._process = process
                if self._cancel_requested and process.poll() is None:
                    process.kill()
            self.resources.attach(lease, process)
            if os.name == "nt":
                from app.runtime.windows_job import WindowsJob
                job = WindowsJob(process.pid)
            try:
                output, errors = process.communicate(payload, timeout=600)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Local upscaling timed out.") from None
            if process.returncode != 0:
                if self._cancel_requested:
                    raise RuntimeError("Local upscaling canceled.")
                if b"gpu_oom:" in errors:
                    raise RuntimeError("Local upscale CUDA memory exhausted at tile=128; source remains selected.")
                raise RuntimeError("Local upscale worker failed: " + errors.decode("utf-8", "replace")[-500:])
            measured = decode_image(output, OUTPUT_LIMITS)
            target = ("PNG", request.width * request.factor, request.height * request.factor)
            if (measured["format"], measured["width"], measured["height"]) != target:
                raise ValueError("Local upscaler output dimensions differ from request.")
            diagnostics = {}
            for line in errors.decode("utf-8", "replace").splitlines():
                if line.startswith("upscale_diagnostics:"):
                    diagnostics = json.loads(line.removeprefix("upscale_diagnostics:"))
            return ImageUpscaleResult(output, "PNG", target[1], target[2],
                                      {"runtime": installed.fingerprint, "sha256": sha256(output).hexdigest(),
                                       "diagnostics": diagnostics})
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.resources.quarantine(lease)
                    raise RuntimeError("Upscale worker cleanup is uncertain; GPU ownership is quarantined.")
            if job is not None:
                job.close()
            if not self.resources.availability().quarantined:
                self.resources.release(lease)
            with self._lock:
                self._process = None
                self._cancel_requested = False

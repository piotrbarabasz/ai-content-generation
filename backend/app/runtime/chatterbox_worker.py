"""Candidate private worker: local V3 weights, D010 resume, D028 OOM outcome."""

import json
import os
from pathlib import Path
import sys

from app.domain.dependencies import canonical_json
from app.domain.generation_job import JobRequest
from app.providers.chatterbox_v3 import _RuntimeBackend
from app.runtime.chatterbox_assets import ChatterboxAssets
from app.runtime.chatterbox_health import check_private_runtime
from app.runtime.chatterbox_voice import prepare_voice
from app.runtime.section_synthesis import generate, inputs, workspace
from app.runtime.worker import WorkerFailure, serve


def enforce_offline():
    """Fail closed on Python network attempts, including non-HF dependencies.

    This is an execution policy for the trusted private process, not a sandbox
    for arbitrary native code. It must never be installed in the application.
    """
    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "urllib.Request"}:
            raise OSError("Managed Chatterbox workers cannot access the network; provision assets explicitly.")
    sys.addaudithook(audit)


def handle(payload, report, canceled):
    health = check_private_runtime(os.environ["AICS_CHATTERBOX_DEVICE"])
    if payload.get("request", {}).get("operation") == "runtime.chatterbox.health":
        # The path is supplied by trusted launch composition, never by the job.
        Path(os.environ["AICS_CHATTERBOX_HEALTH"]).write_text(canonical_json(health.to_payload()), encoding="utf-8")
        return []
    job = JobRequest.from_payload(payload)
    _, expected = inputs(job)
    health.decision().validate(job.request)
    models = ChatterboxAssets(os.environ["AICS_SECTION_MODELS"]).installed()
    if models is None:
        raise ValueError("Install the verified Chatterbox V3 assets before synthesis.")
    oom = [False]

    def load(device):
        import torch
        import torchaudio
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        class Backend(_RuntimeBackend):
            def generate(self, text, **kwargs):
                if oom[0]:
                    raise RuntimeError("GPU OOM; restart required.")
                try:
                    return super().generate(text, **kwargs)
                except torch.cuda.OutOfMemoryError:
                    oom[0] = True
                    raise

        try:
            # No from_pretrained/main/HF network access on the synthesis path.
            model = ChatterboxMultilingualTTS.from_local(models, device=device, t3_model="v3")
            return Backend(model, torchaudio)
        except torch.cuda.OutOfMemoryError:
            oom[0] = True
            raise

    runtime = json.loads(os.environ["AICS_SECTION_RUNTIME"])
    actual, provider = prepare_voice(expected["selection"], expected["max_words"], health, runtime, model_loader=load)
    if canonical_json(actual) != canonical_json(expected):
        raise ValueError("Chatterbox worker identity differs from the frozen request.")
    import torch
    torch.cuda.reset_peak_memory_stats(health.device)
    try:
        result = generate(job, provider, os.environ["AICS_SECTION_WORK"], report, canceled.is_set)
        # Diagnostic evidence is not accepted as a worker-provided artifact path.
        evidence = health.to_payload() | {"peak_allocated_bytes": torch.cuda.max_memory_allocated(health.device),
                                         "peak_reserved_bytes": torch.cuda.max_memory_reserved(health.device)}
        (workspace(os.environ["AICS_SECTION_WORK"], job) / "gpu-evidence.json").write_text(
            canonical_json(evidence), encoding="utf-8")
        return result
    except Exception:
        if oom[0]:
            raise WorkerFailure("gpu_oom", "Chatterbox GPU memory exhausted; valid chunks retained for explicit retry.") from None
        raise


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    enforce_offline()
    def initialize():
        import torch
        import torchaudio
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    raise SystemExit(serve(handle, quiet_handler=True, initialize=initialize))

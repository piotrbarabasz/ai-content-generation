"""Offline D029 process fixture: real composition and chunks, fake model bytes."""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import io
import json
import os
import wave
from app.domain.generation_job import JobRequest
from app.runtime.chatterbox_profile import ChatterboxHealth, profile_fingerprint
from app.runtime.chatterbox_voice import prepare_voice
from app.runtime.section_synthesis import generate, inputs
from app.runtime.worker import WorkerFailure, serve


def handle(payload, report, canceled):
    print("Model log must not corrupt protocol stdout", flush=True)
    os.write(1, b"Native model log must also go to stderr\n")
    health = ChatterboxHealth(profile_fingerprint(), os.environ["AICS_CHATTERBOX_DEVICE"], ("en", "pl"), "fixture", 1024)
    if payload.get("request", {}).get("operation") == "runtime.chatterbox.health":
        Path(os.environ["AICS_CHATTERBOX_HEALTH"]).write_text(json.dumps(health.to_payload()), encoding="utf-8")
        return []
    job = JobRequest.from_payload(payload)
    _, expected = inputs(job)
    health.decision().validate(job.request)
    if os.environ.get("CHATTERBOX_CASE") == "oom":
        raise WorkerFailure("gpu_oom", "Fixture GPU OOM")

    class Backend:
        def generate(self, text, **kwargs):
            if os.environ.get("CHATTERBOX_CASE") == "partial" and text == "Two.":
                raise RuntimeError("Fixture interrupted chunk")
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x01\x00" * 2400)
            return buffer.getvalue()

    actual, provider = prepare_voice(expected["selection"], expected["max_words"], health,
                                     json.loads(os.environ["AICS_SECTION_RUNTIME"]), model_loader=lambda device: Backend())
    if actual != expected:
        raise ValueError("Fixture request identity mismatch")
    return generate(job, provider, os.environ["AICS_SECTION_WORK"], report, canceled.is_set)


raise SystemExit(serve(handle, quiet_handler=True, initialize=lambda: os.write(1, b"Native init log\n")))

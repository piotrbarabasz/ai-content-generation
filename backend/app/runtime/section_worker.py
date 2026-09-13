"""Private-worker dispatch. Paths come only from trusted launch composition."""

import json
import os

from app.domain.dependencies import canonical_json
from app.domain.generation_job import JobRequest
from .section_synthesis import generate, inputs
from .section_voice import prepare_voice


def section_audio(payload, report, canceled):
    job = JobRequest.from_payload(payload)
    _, expected = inputs(job)
    runtime = json.loads(os.environ["AICS_SECTION_RUNTIME"])
    actual, provider = prepare_voice(expected["selection"], expected["max_words"], os.environ["AICS_SECTION_MODELS"], runtime)
    if canonical_json(actual) != canonical_json(expected):
        raise ValueError("Worker runtime/voice identity differs from the enqueued request.")
    return generate(job, provider, os.environ["AICS_SECTION_WORK"], report, canceled.is_set)

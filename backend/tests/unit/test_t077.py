from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

from app.providers.chatterbox_v3 import ChatterboxV3Provider


ROOT = Path(__file__).resolve().parents[3]


def _wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\0\0" * 2_400)
    return buffer.getvalue()


class RecordingBackend:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, text: str, **kwargs):
        self.calls.append((text, kwargs))
        return _wav()


def test_chatterbox_english_capability_and_forwarding_use_fake_runtime() -> None:
    backend = RecordingBackend()
    provider = ChatterboxV3Provider(
        language_id="en", device="cpu", model_loader=lambda _device: backend
    )
    assert provider.capabilities().supports_language("en")
    identity = provider.effective_synthesis_identity({"language_id": "en"})
    result = provider.synthesize("Original English narration.", {"language_id": "en"})
    assert identity["language_id"] == "en"
    assert identity["model_variant"] == "v3"
    assert identity["voice"] == {"mode": "builtin"}
    assert backend.calls[0][1]["language_id"] == "en"
    assert result.metadata["language_id"] == "en"


def test_english_fixture_metadata_and_manual_command_are_reproducible() -> None:
    fixture = ROOT / "backend/tests/fixtures/narrations/story_en_01_1min.txt"
    metadata = json.loads(
        (fixture.parent / "metadata.json").read_text(encoding="utf-8")
    )
    record = next(item for item in metadata["fixtures"] if item["file"] == fixture.name)
    payload = fixture.read_bytes()
    assert record["language"] == "en"
    assert record["actual_word_count"] == 157
    assert hashlib.sha256(payload).hexdigest() == record["sha256"]
    guide = (ROOT / "docs/tts/CHATTERBOX_ENGLISH_BASELINE.md").read_text(encoding="utf-8")
    assert ".venv-tts311\\Scripts\\python.exe" in guide
    assert "--provider chatterbox_v3" in guide
    assert "--language en" in guide
    assert "--report" in guide

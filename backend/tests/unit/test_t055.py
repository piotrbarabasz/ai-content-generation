from __future__ import annotations

import json
import sys

import pytest

from app.tooling import tts_smoke


def test_mock_smoke_writes_valid_wav_and_report(tmp_path):
    output = tmp_path / "nested" / "speech.wav"
    assert tts_smoke.main(["--text", "hello smoke test", "--output", str(output)]) == 0
    report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert output.exists()
    assert report["provider"] == "mock"
    assert report["model_variant"] == "v3"
    assert report["word_count"] == 3
    assert report["sample_rate"] == 24_000
    assert report["voice"] == "builtin"


def test_input_text_file_and_settings_are_forwarded(tmp_path, monkeypatch):
    text_file = tmp_path / "fixture.txt"
    text_file.write_text("Cześć z pliku", encoding="utf-8")
    output = tmp_path / "speech.wav"
    captured = {}

    class FakeProvider:
        provider_name = "chatterbox_v3"

        def synthesize(self, text, voice_config):
            captured["text"] = text
            captured["voice_config"] = voice_config
            from app.providers.mock_tts import MockTTSProvider

            return MockTTSProvider().synthesize(text, voice_config)

    monkeypatch.setattr(tts_smoke, "_create_provider", lambda args: FakeProvider())
    assert tts_smoke.main([
        "--provider", "chatterbox_v3", "--input-text-file", str(text_file),
        "--output", str(output), "--language", "pl", "--device", "cuda",
        "--audio-prompt", str(tmp_path / "speaker.wav"), "--temperature", "0.7",
    ]) == 0
    assert captured["text"] == "Cześć z pliku"
    assert captured["voice_config"] == {
        "language_id": "pl", "audio_prompt_path": str(tmp_path / "speaker.wav"), "temperature": 0.7,
    }


def test_unreadable_or_blank_input_file_returns_nonzero(tmp_path, capsys):
    missing = tmp_path / "missing.txt"
    assert tts_smoke.main(["--input-text-file", str(missing), "--output", str(tmp_path / "speech.wav")]) == 1
    assert "Cannot read UTF-8 input text file" in capsys.readouterr().err
    blank = tmp_path / "blank.txt"
    blank.write_text(" \n", encoding="utf-8")
    assert tts_smoke.main(["--input-text-file", str(blank), "--output", str(tmp_path / "speech.wav")]) == 1
    assert "must not be empty" in capsys.readouterr().err


def test_invalid_text_returns_nonzero(tmp_path, capsys):
    code = tts_smoke.main(["--text", "   ", "--output", str(tmp_path / "speech.wav")])
    assert code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_existing_output_requires_explicit_overwrite(tmp_path, capsys):
    output = tmp_path / "speech.wav"
    output.write_bytes(b"existing")
    assert tts_smoke.main(["--text", "hello", "--output", str(output)]) == 1
    assert "--overwrite" in capsys.readouterr().err


def test_provider_failure_returns_nonzero(tmp_path, monkeypatch, capsys):
    def fail(_args):
        raise RuntimeError("provider boom")
    monkeypatch.setattr(tts_smoke, "_create_provider", fail)
    assert tts_smoke.main(["--text", "hello", "--output", str(tmp_path / "speech.wav")]) == 1
    assert "provider boom" in capsys.readouterr().err


def test_help_does_not_import_optional_runtime(monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "chatterbox", raising=False)
    with pytest.raises(SystemExit, match="0"):
        tts_smoke.main(["--help"])
    assert "chatterbox" not in sys.modules
    assert "--provider" in capsys.readouterr().out

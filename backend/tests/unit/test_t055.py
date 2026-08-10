from __future__ import annotations

import json
import sys

import pytest

from app.providers.tts_settings import TTSSettingsError
from app.tooling import tts_smoke


def test_mock_smoke_writes_valid_wav_and_report(tmp_path):
    output = tmp_path / "nested" / "speech.wav"
    assert tts_smoke.main(["--text", "hello smoke test", "--output", str(output)]) == 0
    report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert output.exists()
    assert report["provider"] == "mock"
    assert report["model_variant"] == "mock"
    assert report["word_count"] == 3
    assert report["channels"] == 1
    assert report["sample_width"] == 2
    assert report["sample_rate"] == 24_000
    assert report["compression_type"] == "NONE"
    assert report["frame_count"] > 0
    assert report["duration_seconds"] > 0
    assert report["voice"] == "builtin"
    assert report["tempo"] == 1.0
    assert report["post_processing"]["processor"] == "none"


def test_tempo_is_parsed_processed_once_and_reported(tmp_path, monkeypatch):
    output = tmp_path / "speech.wav"
    calls = []
    original = tts_smoke.process_pcm_wav_tempo

    def capture(audio_bytes, tempo):
        calls.append(tempo)
        return original(audio_bytes, 1.0)

    monkeypatch.setattr(tts_smoke, "process_pcm_wav_tempo", capture)
    assert tts_smoke.main(
        ["--text", "slow narration", "--output", str(output), "--tempo", "0.92"]
    ) == 0
    report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert calls == [0.92]
    assert report["tempo"] == 0.92
    assert report["post_processing"]["processor"] == "none"


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

            return MockTTSProvider("chatterbox_v3").synthesize(text, {})

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


def _capture_smoke_provider_settings(monkeypatch, args):
    captured = {}

    def capture(provider_config, **_kwargs):
        captured.update(provider_config.settings)
        return object()

    monkeypatch.setattr(tts_smoke, "build_tts_provider", capture)
    tts_smoke._create_provider(args)
    return captured


def test_smoke_default_mock_forwards_only_common_settings(tmp_path, monkeypatch):
    args = tts_smoke.build_parser().parse_args(
        ["--text", "hello", "--output", str(tmp_path / "speech.wav")]
    )

    captured = _capture_smoke_provider_settings(monkeypatch, args)

    assert captured == {
        "provider": "mock",
        "usage_policy": "production",
        "device": "cpu",
        "language_id": "pl",
    }
    assert not set(tts_smoke._KNOBS).intersection(captured)
    assert not {"model_key", "model_path", *tts_smoke._PIPER_KNOBS}.intersection(captured)


def test_smoke_selects_only_chatterbox_or_piper_settings(tmp_path, monkeypatch):
    chatterbox_args = tts_smoke.build_parser().parse_args(
        [
            "--provider", "chatterbox_v3",
            "--text", "hello",
            "--output", str(tmp_path / "chatterbox.wav"),
            "--model-variant", "v3",
            "--cfg-weight", "0.25",
            "--temperature", "0.7",
        ]
    )
    chatterbox = _capture_smoke_provider_settings(monkeypatch, chatterbox_args)
    assert chatterbox["model_variant"] == "v3"
    assert chatterbox["cfg_weight"] == 0.25
    assert chatterbox["temperature"] == 0.7
    assert not {"model_key", "model_path", *tts_smoke._PIPER_KNOBS}.intersection(chatterbox)

    piper_args = tts_smoke.build_parser().parse_args(
        [
            "--provider", "piper",
            "--text", "cześć",
            "--output", str(tmp_path / "piper.wav"),
            "--model-key", "pl_PL-gosia-medium",
            "--length-scale", "1.25",
            "--volume", "0.75",
            "--noise-scale", "0.2",
            "--noise-w-scale", "0.9",
        ]
    )
    piper = _capture_smoke_provider_settings(monkeypatch, piper_args)
    assert piper["model_key"] == "pl_PL-gosia-medium"
    assert piper["length_scale"] == 1.25
    assert piper["volume"] == 0.75
    assert piper["noise_scale"] == 0.2
    assert piper["noise_w_scale"] == 0.9
    assert not {"audio_prompt_path", "model_variant", *tts_smoke._KNOBS}.intersection(piper)


@pytest.mark.parametrize(
    ("provider", "flag", "value", "message"),
    [
        ("mock", "--length-scale", "1.0", "only supported by Piper"),
        ("chatterbox_v3", "--length-scale", "1.0", "only supported by Piper"),
        ("piper", "--cfg-weight", "0.25", "not supported by Piper"),
    ],
)
def test_smoke_rejects_explicit_incompatible_provider_flags(
    tmp_path,
    provider,
    flag,
    value,
    message,
):
    args = tts_smoke.build_parser().parse_args(
        [
            "--provider", provider,
            "--text", "hello",
            "--output", str(tmp_path / "speech.wav"),
            flag, value,
        ]
    )

    with pytest.raises(TTSSettingsError, match=message):
        tts_smoke._create_provider(args)

from __future__ import annotations

import io
import json
import os
import tempfile
import wave
from pathlib import Path

import pytest

from app.domain.enums import ProviderType
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.tooling import tts_compare


_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "docs" / "tts" / "PROVIDER_COMPARISON.md"
_SEED = 20260805


def _wav_bytes(*, sample_rate: int = 24_000, frames: int = 24) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


class RecordingComparisonProvider:
    provider_type = ProviderType.TTS

    def __init__(self, provider_name: str, *, profile_key: str, voice_mode: str, fail: bool = False) -> None:
        self.provider_name = provider_name
        self.profile_key = profile_key
        self.voice_mode = voice_mode
        self.fail = fail
        self.identity_calls: list[dict[str, object]] = []
        self.synthesis_calls: list[tuple[str, dict[str, object]]] = []

    def capabilities(self) -> TTSCapabilities:
        usage_policy = "evaluation_only" if self.provider_name == "xtts_v2_eval" else "production"
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("pl",),
            voice_modes=("builtin", "catalog", "reference"),
            reference_audio_required=self.provider_name == "xtts_v2_eval",
            speaking_rate_supported=False,
            usage_policy=usage_policy,
        )

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        self.identity_calls.append(config)
        return {
            "provider": self.provider_name,
            "model_variant": "demo",
            "device": "cpu",
            "language_id": config.get("language_id", "pl"),
            "generation_settings": {"seed": config.get("seed")},
            "voice": {
                "mode": self.voice_mode,
                "profile_key": self.profile_key,
            },
        }

    def synthesize(self, text, voice_config=None):
        config = dict(voice_config or {})
        self.synthesis_calls.append((text, config))
        if self.fail:
            raise RuntimeError(f"profile boom: {self.profile_key}")
        return TTSSynthesisResult(
            audio_bytes=_wav_bytes(),
            sample_rate=24_000,
            duration_seconds=0.001,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={
                "voice": {
                    "mode": self.voice_mode,
                    "profile_key": self.profile_key,
                }
            },
        )


def _fake_build_tts_provider(
    seen: dict[str, RecordingComparisonProvider],
    *,
    build_order: list[str] | None = None,
):
    def _build(provider_config, *, registry=None, provider_factories=None):
        provider_name = provider_config.provider_name
        settings = dict(provider_config.settings)
        if provider_name == "piper":
            profile_key = str(settings["model_key"])
            provider = RecordingComparisonProvider(
                provider_name,
                profile_key=profile_key,
                voice_mode="catalog",
                fail=profile_key == "pl_PL-darkman-medium",
            )
        elif provider_name == "chatterbox_v3":
            provider = RecordingComparisonProvider(
                provider_name,
                profile_key="builtin",
                voice_mode="builtin",
            )
        elif provider_name == "xtts_v2_eval":
            provider = RecordingComparisonProvider(
                provider_name,
                profile_key=str(settings["approved_label"]),
                voice_mode="reference",
            )
        else:
            provider = RecordingComparisonProvider(
                provider_name,
                profile_key=provider_name,
                voice_mode="builtin",
            )
        seen[provider_name] = provider
        if build_order is not None:
            build_order.append(provider.profile_key)
        return provider

    return _build


def test_manifest_parses_curated_profiles_and_rejects_duplicate_ids() -> None:
    manifest = tts_compare.load_comparison_manifest(_MANIFEST_PATH)

    assert manifest.seed == _SEED
    assert manifest.default_input_text_file == (
        Path(__file__).resolve().parents[3] / "backend" / "tests" / "fixtures" / "narrations" / "story_01_1min.txt"
    )
    assert manifest.scoring_template == (
        "naturalness",
        "polish_pronunciation",
        "pace",
        "timbre",
        "expression",
        "artifacts",
    )
    assert tuple(profile.profile_id for profile in manifest.profiles) == (
        "chatterbox-neutral",
        "piper-pl_PL-bass-high",
        "piper-pl_PL-darkman-medium",
        "piper-pl_PL-gosia-medium",
        "piper-pl_PL-mc_speech-medium",
        "piper-pl_PL-mls_6892-low",
        "xtts-pl-reference",
    )
    assert tuple(profile.profile_id for profile in tts_compare.default_profiles()) == (
        "chatterbox-neutral",
        "piper-pl_PL-bass-high",
        "piper-pl_PL-darkman-medium",
        "piper-pl_PL-gosia-medium",
        "piper-pl_PL-mc_speech-medium",
        "piper-pl_PL-mls_6892-low",
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        duplicate_manifest = Path(temp_dir) / "duplicate-manifest.md"
        duplicate_manifest.write_text(
            """# Duplicate manifest

```json
{
  "version": 1,
  "default_input_text_file": "../../backend/tests/fixtures/narrations/story_01_1min.txt",
  "seed": 1,
  "profiles": [
    {
      "profile_id": "dup",
      "label": "Dup 1",
      "provider": "mock",
      "settings": {}
    },
    {
      "profile_id": "dup",
      "label": "Dup 2",
      "provider": "mock",
      "settings": {}
    }
  ],
  "scoring_template": ["naturalness"]
}
```
""",
            encoding="utf-8",
        )

        with pytest.raises(tts_compare.TTSComparisonError, match="duplicate profile id"):
            tts_compare.load_comparison_manifest(duplicate_manifest)


def test_comparison_runner_propagates_seed_normalizes_text_and_records_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, RecordingComparisonProvider] = {}
    monkeypatch.setattr(tts_compare, "build_tts_provider", _fake_build_tts_provider(seen))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        input_text = temp_root / "comparison-input.txt"
        input_text.write_text("  Witaj   świecie\n\n  z   kolejką  ", encoding="utf-8")
        output_dir = temp_root / "output"

        summary = tts_compare.run(
            tts_compare.build_parser().parse_args(
                [
                    "--manifest",
                    str(_MANIFEST_PATH),
                    "--input-text-file",
                    str(input_text),
                    "--output-dir",
                    str(output_dir),
                    "--profile",
                    "piper-pl_PL-gosia-medium",
                ]
            )
        )

        assert summary["normalized_text"] == "Witaj świecie z kolejką"
        assert summary["seed"] == _SEED
        assert summary["summary"] == {
            "profile_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
        }
        assert not os.path.isabs(summary["output_dir"])
        assert not os.path.isabs(summary["manifest_path"])
        assert not os.path.isabs(summary["input_text_file"])
        assert summary["scoring_template"] == [
            "naturalness",
            "polish_pronunciation",
            "pace",
            "timbre",
            "expression",
            "artifacts",
        ]

        profile = summary["profiles"][0]
        assert profile["status"] == "completed"
        assert profile["seed"] == _SEED
        assert profile["generation_wall_time_seconds"] is not None
        assert profile["audio_duration_seconds"] == 0.001
        assert profile["real_time_factor"] > 0
        assert profile["pcm_parameters"] == {
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 24_000,
            "compression_type": "NONE",
            "frame_count": 24,
        }
        assert profile["output_wav"] == "profiles/piper-pl_PL-gosia-medium/speech.wav"
        assert profile["report_path"] == "profiles/piper-pl_PL-gosia-medium/report.json"
        assert profile["effective_synthesis_identity"]["generation_settings"]["seed"] == _SEED
        assert seen["piper"].identity_calls[0]["seed"] == _SEED
        assert seen["piper"].synthesis_calls[0][1]["seed"] == _SEED
        assert (output_dir / "profiles" / "piper-pl_PL-gosia-medium" / "speech.wav").exists()
        assert (output_dir / "profiles" / "piper-pl_PL-gosia-medium" / "report.json").exists()
        assert summary["playlist_path"] == "playlist.m3u8"
        assert Path(output_dir / summary["playlist_path"]).read_text(encoding="utf-8").splitlines() == [
            "#EXTM3U",
            "#EXTINF:-1,Piper pl_PL-gosia-medium",
            "profiles/piper-pl_PL-gosia-medium/speech.wav",
        ]


def test_comparison_runner_skips_xtts_without_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):  # pragma: no cover - defensive guard in the test.
        raise AssertionError("XTTS should have been skipped before provider construction.")

    monkeypatch.setattr(tts_compare, "build_tts_provider", fail_if_called)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        output_dir = temp_root / "output"
        summary = tts_compare.run(
            tts_compare.build_parser().parse_args(
                [
                    "--manifest",
                    str(_MANIFEST_PATH),
                    "--output-dir",
                    str(output_dir),
                    "--profile",
                    "xtts-pl-reference",
                ]
            )
        )

        assert summary["summary"] == {
            "profile_count": 1,
            "completed_count": 0,
            "failed_count": 0,
            "skipped_count": 1,
        }
        profile = summary["profiles"][0]
        assert profile["status"] == "skipped"
        assert "approved reference" in profile["reason"]
        assert profile["output_wav"] == "profiles/xtts-pl-reference/speech.wav"
        assert profile["report_path"] == "profiles/xtts-pl-reference/report.json"
        assert Path(output_dir / "profiles" / "xtts-pl-reference" / "report.json").exists()
        assert Path(output_dir / "playlist.m3u8").read_text(encoding="utf-8").splitlines() == ["#EXTM3U"]


def test_comparison_runner_continues_after_profile_failure_and_redacts_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, RecordingComparisonProvider] = {}
    build_order: list[str] = []
    monkeypatch.setattr(
        tts_compare,
        "build_tts_provider",
        _fake_build_tts_provider(seen, build_order=build_order),
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        input_text = temp_root / "comparison-input.txt"
        input_text.write_text("Witaj świecie", encoding="utf-8")
        output_dir = temp_root / "comparison-output"

        summary = tts_compare.run(
            tts_compare.build_parser().parse_args(
                [
                    "--manifest",
                    str(_MANIFEST_PATH),
                    "--input-text-file",
                    str(input_text),
                    "--output-dir",
                    str(output_dir),
                    "--profile",
                    "piper-pl_PL-bass-high",
                    "--profile",
                    "piper-pl_PL-darkman-medium",
                    "--profile",
                    "piper-pl_PL-gosia-medium",
                ]
            )
        )

        assert summary["summary"] == {
            "profile_count": 3,
            "completed_count": 2,
            "failed_count": 1,
            "skipped_count": 0,
        }
        assert not os.path.isabs(summary["output_dir"])
        assert not os.path.isabs(summary["manifest_path"])
        assert not os.path.isabs(summary["input_text_file"])
        assert summary["profiles"][0]["status"] == "completed"
        assert summary["profiles"][1]["status"] == "failed"
        assert "profile boom" in summary["profiles"][1]["reason"]
        assert summary["profiles"][2]["status"] == "completed"
        assert summary["profiles"][1]["output_wav"] == "profiles/piper-pl_PL-darkman-medium/speech.wav"
        assert summary["profiles"][1]["report_path"] == "profiles/piper-pl_PL-darkman-medium/report.json"
        assert Path(output_dir / "profiles" / "piper-pl_PL-bass-high" / "speech.wav").exists()
        assert Path(output_dir / "profiles" / "piper-pl_PL-bass-high" / "report.json").exists()
        assert Path(output_dir / "profiles" / "piper-pl_PL-darkman-medium" / "report.json").exists()
        assert Path(output_dir / "profiles" / "piper-pl_PL-gosia-medium" / "speech.wav").exists()
        assert Path(output_dir / "playlist.m3u8").read_text(encoding="utf-8").splitlines() == [
            "#EXTM3U",
            "#EXTINF:-1,Piper pl_PL-bass-high",
            "profiles/piper-pl_PL-bass-high/speech.wav",
            "#EXTINF:-1,Piper pl_PL-gosia-medium",
            "profiles/piper-pl_PL-gosia-medium/speech.wav",
        ]
        assert build_order == [
            "pl_PL-bass-high",
            "pl_PL-darkman-medium",
            "pl_PL-gosia-medium",
        ]

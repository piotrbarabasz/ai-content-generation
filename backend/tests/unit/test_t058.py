from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.tooling import tts_smoke
from app.tts.benchmark import build_benchmark_report
from app.tts.manifest import AudioParameters, ChunkManifest, SynthesisManifest


def _manifest(*, duration: float = 2.0, failed: list[str] | None = None) -> SynthesisManifest:
    parameters = AudioParameters(1, 2, 24_000, "NONE", int(duration * 24_000))
    chunk = ChunkManifest("chunk-1", 0, "completed", "input", "config", "text", "checksum", duration, parameters)
    return SynthesisManifest(
        config_hash="config", chunks={chunk.chunk_id: chunk}, final_status="completed",
        final_checksum="final-checksum", final_duration_seconds=duration,
        final_audio_parameters=parameters, failed_chunk_ids=failed or [],
    )


def test_benchmark_calculates_rounded_manifest_metrics_and_serializes_stably():
    report = build_benchmark_report(
        _manifest(), provider="mock", model="v3", device="cpu", language="pl",
        word_count=4, generation_wall_time_seconds=1.23456789,
    )
    assert report.generation_wall_time_seconds == 1.234568
    assert report.audio_duration_seconds == 2.0
    assert report.real_time_factor == 0.617284
    assert report.to_payload()["output_checksum"] == "final-checksum"
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        path = Path(directory) / "benchmark.json"
        report.save(path)
        assert json.loads(path.read_text(encoding="utf-8")) == report.to_payload()


def test_benchmark_handles_zero_duration_and_partial_failures_explicitly():
    manifest = _manifest(duration=0.0, failed=["chunk-z", "chunk-a"])
    manifest.final_status = "failed"
    manifest.final_checksum = None
    report = build_benchmark_report(
        manifest, provider="mock", model="v3", device="cpu", language="pl",
        word_count=0, generation_wall_time_seconds=0.5,
    )
    assert report.audio_duration_seconds == 0.0
    assert report.real_time_factor is None
    assert report.output_checksum is None
    assert report.failed_chunk_ids == ("chunk-a", "chunk-z")


def test_smoke_runner_emits_benchmark_fields():
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        output = Path(directory) / "speech.wav"
        assert tts_smoke.main(["--text", "one two", "--output", str(output)]) == 0
        report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        assert report["chunk_count"] == 1
        assert report["failed_chunk_ids"] == []
        assert report["output_checksum"] == report["checksum_sha256"]
        assert report["real_time_factor"] is not None

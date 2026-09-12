"""Offline subprocess contract tests; no Qt or encoder in the default test environment."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest


ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / "backend/app/desktop/spike.py"


def worker(payload):
    process = subprocess.Popen([sys.executable, "-S", str(ENTRY), "--worker"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(payload.encode("ascii"), timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    return process, json.loads(stdout), stderr


@pytest.mark.parametrize("value", ["D002", "", "x" * 256, "Zażółć gęślą jaźń"])
def test_worker_echoes_once_and_exits(value):
    process, reply, stderr = worker(json.dumps({"op": "ping", "value": value}) + "\n")
    assert process.returncode == 0
    assert reply["ok"] is True
    assert reply["echo"] == value
    # Windows venv launchers may start a second process for the interpreter.
    assert isinstance(reply["pid"], int) and reply["pid"] > 0 and reply["pid"] != os.getpid()
    assert stderr == b""


@pytest.mark.parametrize("payload", ["", "{", "[]", "null", '{"op":"stop"}',
                                     '{"op":"ping","value":42}',
                                     json.dumps({"op": "ping", "value": "x" * 257})])
def test_worker_rejects_invalid_request_and_exits(payload):
    process, reply, stderr = worker(payload)
    assert process.returncode == 2
    assert reply["ok"] is False
    assert reply["error"]
    assert stderr == b""


def test_smoke_requires_report_before_importing_qt():
    result = subprocess.run([sys.executable, str(ENTRY), "--smoke"], capture_output=True, timeout=10)
    assert result.returncode == 2
    assert b"--smoke requires --report" in result.stderr


def test_compiled_worker_uses_relocated_entry_executable(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("spike", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executable = tmp_path / "Zażółć test" / "renamed.exe"
    monkeypatch.setattr(module, "__compiled__", object(), raising=False)
    monkeypatch.setattr(sys, "argv", [str(executable)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "absent-python.exe"))
    assert module.worker_command() == [str(executable.resolve()), "--worker"]


def test_synthetic_tone_is_repeatable_two_second_pcm(tmp_path):
    spec = importlib.util.spec_from_file_location("fixtures", ROOT / "packaging/d002/generate_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first, second = tmp_path / "one.wav", tmp_path / "two.wav"
    module.write_tone(first)
    module.write_tone(second)
    assert first.read_bytes() == second.read_bytes()
    with wave.open(str(first), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (1, 2, 48000, 96000)
        samples = struct.unpack("<96000h", audio.readframes(96000))
    assert 0 < max(samples) <= 2000
    assert -2000 <= min(samples) < 0

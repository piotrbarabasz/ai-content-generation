"""Fixed profile and provider-independent desktop rendering contract."""

from dataclasses import replace
import subprocess
import sys

import pytest

from app.domain.render_result import render_request
from app.domain.timeline import OutputTimebase
from tests.unit.test_timeline import compile_values, media


def test_snapshot_and_executable_identity_are_pinned():
    timeline = compile_values(media())
    one = render_request(timeline, {"binary": "one"})
    assert one != render_request(timeline, {"binary": "two"})
    assert one != render_request(replace(timeline, fit_policy="fill"), {"binary": "one"})
    assert {edge.artifact_id for edge in one.inputs} == {"audio-a", "image-a"}
    assert next(edge.key for edge in one.inputs if edge.name == "audio:0") == "section:section-a:audio:raw"


def test_non_profile_fps_is_explicitly_rejected():
    timeline = compile_values(media(), timebase=OutputTimebase(1001, 30000))
    with pytest.raises(ValueError, match="25 FPS"):
        render_request(timeline, {})


def test_application_import_does_not_load_concrete_media_adapters():
    command = """
import sys
from app.application.video_render import VideoRenderService
for name in ('app.providers', 'app.storage', 'app.runtime', 'sqlite3', 'PIL', 'PySide6'):
    assert not any(m == name or m.startswith(name + '.') for m in sys.modules), name
"""
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

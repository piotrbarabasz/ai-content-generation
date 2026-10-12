import asyncio
import sys

import pytest

from app.runtime.media_process import MediaProcess


def test_existing_stdout_and_optional_bounded_stderr_capture(tmp_path):
    command = [sys.executable, "-c", "import sys; print('stdout'); print('measurement',file=sys.stderr)"]
    expected = b"stdout\r\n" if sys.platform == "win32" else b"stdout\n"
    assert asyncio.run(MediaProcess().run(command, cwd=tmp_path)) == expected
    raw = asyncio.run(MediaProcess().run(command, cwd=tmp_path, capture_stderr=True))
    assert raw.strip() == b"measurement"


def test_packet_stdout_streaming_avoids_buffering_large_reports(tmp_path):
    command = [sys.executable, "-c", "for i in range(5000): print('x'*300)"]
    lines = []
    assert asyncio.run(MediaProcess().run(command, cwd=tmp_path, capture_stdout=False, on_line=lambda line: lines.append(len(line)))) == b""
    assert len(lines) == 5000 and all(n == 300 for n in lines)
    with pytest.raises(ValueError, match="stdout exceeded"):
        asyncio.run(MediaProcess().run(command, cwd=tmp_path))

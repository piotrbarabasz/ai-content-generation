"""Actual child processes prove deadline/cancel/pipe handling; no media or network."""

import asyncio
from pathlib import Path
import sys

import pytest

from app.runtime.media_process import MediaProcess, RenderCanceled


PYTHON = str(Path(getattr(sys, "_base_executable", sys.executable)))


def test_exit_code_and_bounded_stderr(tmp_path):
    runner = MediaProcess(timeout=5)
    command = [PYTHON, "-c", "import sys; sys.stderr.write('x'*90000); sys.exit(3)"]
    with pytest.raises(RuntimeError, match="exited 3") as error:
        asyncio.run(runner.run(command, cwd=tmp_path))
    assert len(str(error.value)) < 66000


def test_real_child_progress_does_not_block_event_loop(tmp_path):
    async def scenario():
        seen, ticks = [], []
        async def heartbeat():
            for i in range(10):
                ticks.append(i)
                await asyncio.sleep(0.01)
        result, _ = await asyncio.gather(MediaProcess(timeout=5).run(
            [PYTHON, "-u", "-c", "import time; print('frame=1'); time.sleep(.2); print('frame=2')"],
            cwd=tmp_path, on_line=seen.append), heartbeat())
        assert seen == ["frame=1", "frame=2"] and len(ticks) == 10 and b"frame=2" in result
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["deadline", "cancel", "task_cancel"])
def test_child_is_stopped_on_deadline_and_cancellation(tmp_path, mode):
    marker = tmp_path / "should-not-exist"
    async def scenario():
        canceled = False
        command = [PYTHON, "-c", "import time,pathlib; time.sleep(2); pathlib.Path('should-not-exist').write_text('bad')"]
        task = asyncio.create_task(MediaProcess(timeout=.2 if mode == "deadline" else 5).run(
            command, cwd=tmp_path, canceled=lambda: canceled))
        await asyncio.sleep(.1)
        if mode == "cancel":
            canceled = True
        elif mode == "task_cancel":
            task.cancel()
        with pytest.raises((TimeoutError, RenderCanceled, asyncio.CancelledError)):
            await task
        await asyncio.sleep(2)
        assert not marker.exists()
    asyncio.run(scenario())


def test_oversized_stdout_fails_instead_of_buffering_forever(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(MediaProcess(timeout=5).run(
            [PYTHON, "-c", "print('x'*70000)"], cwd=tmp_path))

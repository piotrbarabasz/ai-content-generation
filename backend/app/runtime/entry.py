"""Fixed source/standalone diagnostic entry point; no job-supplied commands."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.runtime.worker import serve


if __name__ == "__main__":
    raise SystemExit(serve())

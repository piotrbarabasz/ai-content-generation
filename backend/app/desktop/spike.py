"""Run directly: python backend/app/desktop/spike.py --help.

The same standalone executable starts its --worker child. Qt is imported only
on the UI path, so the packaged IPC check does not need a second Python install.
"""

import argparse
from pathlib import Path
import sys


def worker_command() -> list[str]:
    compiled = "__compiled__" in globals() or getattr(sys, "frozen", False)
    # Nuitka's sys.executable may name an absent Python inside the bundle.
    # argv[0] identifies this standalone entry point, including after relocation.
    launch = [str(Path(sys.argv[0]).resolve())] if compiled else [sys.executable, str(Path(__file__).resolve())]
    return launch + ["--worker"]


def main() -> int:
    parser = argparse.ArgumentParser(description="D002 desktop packaging spike")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).parent / "fixtures")
    parser.add_argument("--smoke", action="store_true", help="Run muted automated media/IPC checks")
    parser.add_argument("--report", type=Path, help="Write smoke evidence JSON to this explicit path")
    args = parser.parse_args()
    if args.worker:
        from spike_worker import main as worker_main

        return worker_main()
    if args.smoke and args.report is None:
        parser.error("--smoke requires --report")
    from spike_window import run

    return run(args.fixtures.resolve(), worker_command(), args.smoke, args.report)


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the stdlib-only D007 diagnostic worker using D002's pinned Nuitka toolchain."""

import argparse
from importlib.metadata import version
from pathlib import Path
import os
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New ignored build directory")
    args = parser.parse_args()
    if sys.platform != "win32" or version("Nuitka") != "4.1.1":
        parser.error("Use Windows with the pinned packaging/d002/requirements.txt toolchain.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "source"
    package = stage / "app/runtime"
    package.mkdir(parents=True)
    (stage / "app/__init__.py").write_text("", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    for name in ("__init__.py", "protocol.py", "worker.py", "entry.py"):
        shutil.copy2(root / "backend/app/runtime" / name, package / name)
    environment = dict(os.environ, PYTHONPATH=str(stage))
    with (output / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "nuitka", "--standalone", "--msvc=14.3",
                        "--assume-yes-for-downloads", "--windows-console-mode=force",
                        "--include-package=app.runtime", f"--output-dir={output}",
                        "--output-filename=d007-worker.exe", str(package / "entry.py")],
                       cwd=stage, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    executable = output / "entry.dist/d007-worker.exe"
    if not executable.is_file():
        raise RuntimeError("Standalone worker executable was not produced.")
    print(f"Copy the entire bundle: {executable.parent}")


if __name__ == "__main__":
    main()

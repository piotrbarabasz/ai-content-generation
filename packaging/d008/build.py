"""Build the offline provisioning smoke with D002's pinned Nuitka toolchain."""

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" or version("Nuitka") != "4.1.1":
        parser.error("Use Windows and packaging/d002/requirements.txt's pinned toolchain.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "source"
    package = stage / "app/runtime"
    package.mkdir(parents=True)
    (stage / "app/__init__.py").write_text("", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    runtime = root / "backend/app/runtime"
    for name in ("__init__.py", "protocol.py", "profiles.py", "profile_catalog.py", "provisioning.py",
                 "piper_health.py", "native_piper.py", "windows_job.py", "worker_bundle.py", "piper_cpu_windows_x64.json"):
        shutil.copy2(runtime / name, package / name)
    # Preserve source bytes, including newlines, identically to a source install.
    sys.path.insert(0, str(root / "backend"))
    from app.runtime.worker_bundle import source_files
    payload = {name: data.decode("utf-8") for name, data in source_files().items()}
    (package / "worker_sources.json").write_text(json.dumps(payload), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("smoke.py"), stage / "smoke.py")
    env = dict(os.environ, PYTHONPATH=str(stage))
    with (output / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "nuitka", "--standalone", "--msvc=14.3",
                        "--assume-yes-for-downloads", "--windows-console-mode=force",
                        "--include-package=app.runtime", "--include-package-data=app.runtime",
                        f"--output-dir={output}", "--output-filename=d008-smoke.exe", str(stage / "smoke.py")],
                       cwd=stage, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f"Copy the entire bundle: {output / 'smoke.dist'}")


if __name__ == "__main__":
    main()

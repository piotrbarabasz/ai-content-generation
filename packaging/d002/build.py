"""Build only the D002 spike with pyside6-deploy standalone on Windows."""

import argparse
import configparser
from importlib.metadata import distribution, version
from pathlib import Path
import shutil
import subprocess
import sys

from generate_fixtures import generate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/d002/package"),
                        help="New build directory; refuses to reuse existing output")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32" or not shutil.which("dumpbin"):
        parser.error("Use Windows x64 Developer PowerShell for VS 2022 (MSVC and dumpbin required).")
    if version("PySide6") != "6.11.2" or version("Nuitka") != "4.1.1":
        parser.error("Install the pinned packaging/d002/requirements.txt in this environment.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    root = Path(__file__).resolve().parents[2]
    for name in ("spike.py", "spike_window.py", "spike_worker.py"):
        shutil.copy2(root / "backend/app/desktop" / name, source / name)
    generate(source / "fixtures", args.ffmpeg)
    config = configparser.ConfigParser()
    template = distribution("PySide6_Essentials").locate_file("PySide6/scripts/deploy_lib/default.spec")
    config.read(template, encoding="utf-8")
    config["app"].update(title="D002 packaging spike", project_dir=source.as_posix(),
                         input_file=(source / "spike.py").as_posix(), exec_directory=output.as_posix())
    config["python"].update(python_path=Path(sys.executable).as_posix(), packages="Nuitka==4.1.1")
    config["nuitka"].update(mode="standalone", extra_args=(
        '--msvc=14.3 --assume-yes-for-downloads --windows-console-mode=force --noinclude-qt-translations '
        f'"--include-data-dir={(source / "fixtures").as_posix()}=fixtures"'))
    spec = output / "pysidedeploy.spec"
    with spec.open("w", encoding="utf-8") as handle:
        config.write(handle)
    command = [str(Path(sys.executable).with_name("pyside6-deploy.exe")),
               "-c", str(spec), "-f", "--keep-deployment-files"]
    if args.dry_run:
        command.append("--dry-run")
    subprocess.run(command, check=True, cwd=source)
    bundle = output / "D002 packaging spike.dist"
    if not args.dry_run:
        if not (bundle / "spike.exe").is_file():
            raise RuntimeError("Deployment did not produce the expected standalone executable.")
        print(f"Standalone folder (copy the ENTIRE folder): {bundle}")


if __name__ == "__main__":
    main()

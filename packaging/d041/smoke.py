"""Installed D041 launch/media/worker/uninstall-preservation smoke harness."""

from __future__ import annotations

import argparse
from hashlib import file_digest
import json
import os
from pathlib import Path
import subprocess
import tempfile


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--clean-windows", action="store_true")
    args = parser.parse_args()
    root = args.installed_root.resolve(strict=True)
    executable = root / "AIContentStudio.exe"
    ffmpeg, ffprobe = root / "media/ffmpeg.exe", root / "media/ffprobe.exe"
    for path in (executable, ffmpeg, ffprobe, root / "release-manifest.json"):
        if not path.is_file():
            raise FileNotFoundError(path)
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "QT", "QML")) and key.upper() != "VIRTUAL_ENV"}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    probes = {}
    for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        result = subprocess.run([str(path), "-version"], cwd=tempfile.gettempdir(), env=environment,
                                capture_output=True, timeout=60)
        probes[name] = {"exit_code": result.returncode,
                        "first_line": result.stdout.decode("utf-8", "replace").splitlines()[0]}
        if result.returncode:
            raise RuntimeError(f"Bundled {name} failed.")
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    launch_report = args.report.resolve().with_name("desktop-launch.json")
    launch = subprocess.run([str(executable), "--release-smoke", str(launch_report)],
                            cwd=tempfile.gettempdir(), env=environment, capture_output=True, timeout=30)
    if launch.returncode or not launch_report.is_file():
        raise RuntimeError(f"Packaged desktop launch failed with exit {launch.returncode}.")
    launch_payload = json.loads(launch_report.read_text(encoding="utf-8"))
    if not launch_payload.get("window_visible") or not launch_payload.get("ffmpeg") or not launch_payload.get("ffprobe"):
        raise RuntimeError("Packaged desktop did not locate its window and bundled media.")
    report = {
        "schema_version": 1,
        "automated_pass": True,
        "clean_windows_attested": args.clean_windows,
        "installed_root": str(root),
        "application_sha256": sha256(executable),
        "media": probes,
        "desktop_launch": launch_payload,
        "signing": manifest["signing"],
        "system_path_only": True,
        "manual_launch_playback_worker": "pending",
        "uninstall_project_preservation": "pending",
    }
    args.report.resolve().write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

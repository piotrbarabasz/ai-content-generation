"""Install, launch, verify bundled media, uninstall, and preserve a project sentinel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New isolated evidence directory")
    parser.add_argument("--clean-windows", action="store_true")
    args = parser.parse_args()
    installer = args.installer.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    install = output / "Program żółty with spaces"
    project = output / "User project — preserve"
    project.mkdir()
    sentinel = project / "project-sentinel.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/DIR={install}"], check=True, timeout=180)
    smoke_report = output / "installed-smoke.json"
    command = [sys.executable, str(Path(__file__).with_name("smoke.py")),
               "--installed-root", str(install), "--report", str(smoke_report)]
    if args.clean_windows:
        command.append("--clean-windows")
    try:
        subprocess.run(command, check=True, timeout=180)
    finally:
        uninstaller = install / "unins000.exe"
        if uninstaller.is_file():
            subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                           check=True, timeout=180)
    result = {
        "schema_version": 1,
        "automated_pass": sentinel.read_text(encoding="utf-8") == "preserve\n"
        and not (install / "AIContentStudio.exe").exists(),
        "clean_windows_attested": args.clean_windows,
        "installed_smoke": json.loads(smoke_report.read_text(encoding="utf-8")),
        "project_preserved_after_uninstall": sentinel.is_file(),
        "application_removed": not (install / "AIContentStudio.exe").exists(),
    }
    (output / "installer-smoke.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not result["automated_pass"]:
        raise RuntimeError("D041 installer lifecycle smoke failed.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

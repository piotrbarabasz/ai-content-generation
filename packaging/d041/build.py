"""Build the pinned standalone Windows bundle and per-user installer."""

from __future__ import annotations

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from release import audit_bundle, load_lock, verify_ffmpeg, verify_installer_compiler, write_manifest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def render_installer(template: str, *, version_value: str, bundle: Path, output: Path,
                     signed_uninstaller: bool) -> str:
    replacements = {
        "@APP_VERSION@": version_value,
        "@BUNDLE_DIR@": str(bundle).replace("/", "\\"),
        "@OUTPUT_DIR@": str(output).replace("/", "\\"),
        "@SIGNED_UNINSTALLER@": "yes" if signed_uninstaller else "no",
        "@SIGNTOOL_LINE@": "SignTool=release" if signed_uninstaller else "",
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    if "@" in template:
        raise ValueError("Installer template contains an unresolved marker.")
    return template


def run(command: list[str], *, cwd: Path, log: Path) -> None:
    with log.open("w", encoding="utf-8", newline="\n") as output:
        subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--ffmpeg-dir", type=Path, required=True, help="Pinned shared FFmpeg bin directory")
    parser.add_argument("--iscc", type=Path, help="Inno Setup ISCC.exe; omit to build/audit the bundle only")
    parser.add_argument("--sign-thumbprint", help="Certificate-store SHA-1 thumbprint used by SignTool")
    parser.add_argument("--signtool", type=Path, help="SignTool.exe required with --sign-thumbprint")
    parser.add_argument("--timestamp-url", default="http://timestamp.digicert.com")
    args = parser.parse_args()
    if sys.platform != "win32" or version("PySide6") != "6.11.2" or version("Nuitka") != "4.1.1":
        parser.error("Use Windows and the pinned packaging/d041 release environment.")
    if bool(args.sign_thumbprint) != bool(args.signtool):
        parser.error("--sign-thumbprint and --signtool must be supplied together.")
    if args.sign_thumbprint and re.fullmatch(r"[0-9A-Fa-f]{40}", args.sign_thumbprint) is None:
        parser.error("--sign-thumbprint must be exactly 40 hexadecimal characters.")
    if (not args.timestamp_url.startswith(("https://", "http://"))
            or any(character.isspace() for character in args.timestamp_url)):
        parser.error("--timestamp-url must be one whitespace-free HTTP(S) URL.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    lock = load_lock(HERE / "components.lock.json")
    media_files = verify_ffmpeg(args.ffmpeg_dir, lock)
    stage = output / "stage"
    stage.mkdir()
    shutil.copy2(HERE / "launcher.py", stage / "launcher.py")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    command = [
        sys.executable, "-m", "nuitka", "--standalone", "--enable-plugin=pyside6",
        "--msvc=14.3", "--assume-yes-for-downloads", "--windows-console-mode=disable",
        "--noinclude-qt-translations", "--include-qt-plugins=multimedia",
        "--include-package-data=app.runtime",
        f"--output-dir={output}", "--output-filename=AIContentStudio.exe", str(stage / "launcher.py"),
    ]
    with (output / "nuitka.log").open("w", encoding="utf-8", newline="\n") as log:
        subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    bundle = output / "launcher.dist"
    if not (bundle / "AIContentStudio.exe").is_file():
        raise RuntimeError("Nuitka did not produce the expected standalone executable.")
    media = bundle / "media"
    media.mkdir()
    for source in media_files:
        shutil.copy2(source, media / source.name)
    shutil.copy2(HERE / "THIRD-PARTY-NOTICES.txt", bundle / "THIRD-PARTY-NOTICES.txt")

    signed = False
    if args.sign_thumbprint:
        sign = [str(args.signtool.resolve(strict=True)), "sign", "/fd", "SHA256", "/sha1",
                args.sign_thumbprint, "/tr", args.timestamp_url, "/td", "SHA256"]
        subprocess.run(sign + [str(bundle / "AIContentStudio.exe")], check=True)
        signed = True
    inventory = audit_bundle(bundle)
    write_manifest(bundle / "release-manifest.json", lock=lock, inventory=inventory, signed=signed)

    installer = None
    if args.iscc:
        installer_output = output / "installer"
        installer_output.mkdir()
        rendered = render_installer((HERE / "installer.iss.in").read_text(encoding="utf-8"),
                                    version_value=lock["application"]["version"], bundle=bundle,
                                    output=installer_output, signed_uninstaller=signed)
        script = output / "installer.iss"
        script.write_text(rendered, encoding="utf-8", newline="\n")
        iscc = str(verify_installer_compiler(args.iscc, lock))
        command = [iscc]
        if signed:
            sign_command = (f'$q{args.signtool.resolve(strict=True)}$q sign /fd SHA256 /sha1 '
                            f'{args.sign_thumbprint} /tr {args.timestamp_url} /td SHA256 $f')
            command.append(f"/Srelease={sign_command}")
        command.append(str(script))
        run(command, cwd=ROOT, log=output / "inno.log")
        installers = tuple(installer_output.glob("*.exe"))
        if len(installers) != 1:
            raise RuntimeError("Inno Setup did not produce exactly one installer.")
        installer = installers[0]
        if signed:
            subprocess.run([str(args.signtool.resolve(strict=True)), "verify", "/pa", "/all", str(installer)],
                           check=True)
    evidence = {
        "schema_version": 1,
        "bundle": str(bundle),
        "installer": str(installer) if installer else None,
        "signing": "signed" if signed else "unsigned",
        "release_eligible": bool(installer and signed),
        "user_data_preserved_on_uninstall": True,
    }
    (output / "build-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()

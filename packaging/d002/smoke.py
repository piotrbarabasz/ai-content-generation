"""Exercise a relocated Windows bundle without Python/FFmpeg on its PATH.

This is a developer-host check, not proof of clean-machine or audible playback.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / "Zażółć test" / "spike.dist"
    shutil.copytree(args.bundle.resolve(), bundle)
    executable = bundle / "spike.exe"
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "QT", "QML")) and key.upper() != "VIRTUAL_ENV"}
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    checks = []
    for payload, expected_code in [('{"op":"ping","value":"D002"}\n', 0), ("{}\n", 2), ("", 2)]:
        process = subprocess.Popen([str(executable), "--worker"], cwd=output, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(payload.encode(), timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        reply = json.loads(stdout)
        assert process.returncode == expected_code, (process.returncode, stderr)
        assert reply["ok"] is (expected_code == 0), reply
        if expected_code == 0:
            assert reply["echo"] == "D002" and reply["pid"] == process.pid, reply
        checks.append({"exit_code": process.returncode, "reply": reply})
    for name, extra, expected_code in [("media", [], 0),
                                        ("missing-fixtures", ["--fixtures", str(output / "missing")], 1)]:
        report = output / f"{name}.json"
        result = subprocess.run([str(executable), "--smoke", "--report", str(report), *extra],
                                cwd=output, env=env, capture_output=True, timeout=45)
        (output / f"{name}.stderr.txt").write_bytes(result.stderr)
        assert result.returncode == expected_code, (result.returncode, result.stderr)
        evidence = json.loads(report.read_text(encoding="utf-8"))
        assert evidence["automated_pass"] is (expected_code == 0), evidence
    (output / "relocation.json").write_text(json.dumps({
        "automated_pass": True, "worker_cases": checks, "unicode_and_spaces": True,
        "unrelated_working_directory": True, "path_only_system32": True,
        "clean_windows_verified": False, "manual_picture_and_sound_observed": False,
    }, indent=2), encoding="utf-8")
    print(f"PASS: worker, playback, missing-fixture failure and relocation. Evidence: {output}")


if __name__ == "__main__":
    main()

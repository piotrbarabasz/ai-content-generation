"""Canonicalize the two locally built D029 wheels and remove one mutable edge.

The pinned Chatterbox commit declares Perth from the mutable ``master`` branch.
D029 reviewed Perth 1.0.1 instead. This packaging-only metadata rewrite keeps the
upstream Python sources byte-for-byte while making the installed dependency graph
closed and repeatable. RECORD is regenerated before the wheel is pinned.
"""

from __future__ import annotations

import argparse
import base64
import csv
from hashlib import sha256
import io
import os
from pathlib import Path
import tempfile
import zipfile


MUTABLE_PERTH = "Requires-Dist: resemble-perth @ git+https://github.com/resemble-ai/Perth.git@master"
PINNED_PERTH = "Requires-Dist: resemble-perth==1.0.1"


def digest(value: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(sha256(value).digest()).rstrip(b"=").decode("ascii")


def normalize(path: Path) -> None:
    with zipfile.ZipFile(path) as source:
        values = {name: source.read(name) for name in source.namelist() if not name.endswith("/")}
    metadata, = [name for name in values if name.endswith(".dist-info/METADATA")]
    record, = [name for name in values if name.endswith(".dist-info/RECORD")]
    if path.name.startswith("chatterbox_tts-"):
        text = values[metadata].decode("utf-8")
        if text.count(MUTABLE_PERTH) != 1:
            raise ValueError("Pinned Chatterbox wheel does not contain the reviewed mutable Perth edge.")
        values[metadata] = text.replace(MUTABLE_PERTH, PINNED_PERTH).encode("utf-8")
    rows = []
    for name in sorted(values):
        if name == record:
            continue
        rows.append((name, digest(values[name]), str(len(values[name]))))
    rows.append((record, "", ""))
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    values[record] = stream.getvalue().encode("utf-8")
    with tempfile.NamedTemporaryFile(delete=False, dir=path.parent, suffix=".whl") as temporary:
        output = Path(temporary.name)
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for name in sorted(values):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                target.writestr(info, values[name])
        os.replace(output, path)
    finally:
        output.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    for wheel in args.wheels:
        normalize(wheel.resolve(strict=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

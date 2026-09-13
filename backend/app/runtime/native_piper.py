"""App-local DLL extraction recipe for the single reviewed D045 redist binary.

No MSI/EXE installer is executed. Windows expand reads two pinned CAB containers.
Offsets and payload names are recipe data, not a general Burn archive parser.
"""

import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from .profiles import ProfileError


REDIST_SHA256 = "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b"
X64_CAB_SHA256 = "640aa6c516c72444523b8fbe034db46ff4e118ed02705340e3ccb62d426ff040"
LIBRARIES = ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll")


def system_directory() -> Path:
    if os.name != "nt":
        raise ProfileError("Private Piper provisioning requires Windows.")
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Cannot locate the Windows system directory.")
    return Path(buffer.value)


def extract_native(redist: Path, destination: Path, scratch: Path) -> None:
    data = redist.read_bytes()
    if hashlib.sha256(data).hexdigest() != REDIST_SHA256:
        raise ProfileError("No native extraction recipe for this redistributable.")
    scratch.mkdir()
    attached = scratch / "attached.cab"
    attached.write_bytes(data[686152:686152 + 24939223])
    expand = system_directory() / "expand.exe"

    def extract(cab, member):
        subprocess.run([str(expand), str(cab), f"-F:{member}", str(scratch)],
                       check=True, timeout=60, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

    extract(attached, "a12")
    cabinet = scratch / "a12"
    if hashlib.sha256(cabinet.read_bytes()).hexdigest() != X64_CAB_SHA256:
        raise ProfileError("Unexpected native x64 cabinet.")
    for library in LIBRARIES:
        extract(cabinet, library + "_amd64")
        shutil.copyfile(scratch / (library + "_amd64"), destination / library)

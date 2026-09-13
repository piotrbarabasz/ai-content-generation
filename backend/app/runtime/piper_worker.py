"""Private Piper runtime composition: health, diagnostics and D010 section audio."""

import sys

sys.dont_write_bytecode = True

import ctypes
from ctypes import wintypes
from importlib import metadata
import json
from pathlib import Path

from app.runtime.worker import diagnostic, serve


def health(job, report, canceled):
    root = Path(sys.executable).resolve().parent
    profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
    expected = profile["interpreter"]["version"]
    if (sys.platform != "win32" or sys.version.split()[0] != expected
            or ctypes.sizeof(ctypes.c_void_p) != 8 or not sys.flags.isolated
            or not sys.flags.no_site or Path(sys.prefix).resolve() != root
            or any(not Path(p).resolve().is_relative_to(root) for p in sys.path)):
        raise RuntimeError("Interpreter version, architecture or path isolation mismatch.")
    report("interpreter", 1, 1)
    for package in profile["packages"]:
        distribution = metadata.distribution(package["name"])
        if (distribution.version != package["version"]
                or not Path(distribution.locate_file("")).resolve().is_relative_to(root)):
            raise RuntimeError("Private package identity mismatch.")
    report("packages", 1, 1)

    # Imports exercise the real native bindings without downloading/loading a voice.
    import numpy
    import onnxruntime
    import piper
    from piper import espeakbridge

    if "CPUExecutionProvider" not in onnxruntime.get_available_providers():
        raise RuntimeError("ONNX CPU backend is unavailable.")
    for module in (numpy, onnxruntime, piper, espeakbridge):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError("Runtime module escaped the private environment.")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    api.GetModuleHandleW.restype = wintypes.HMODULE
    api.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    api.GetModuleFileNameW.restype = wintypes.DWORD
    for name in profile["native_runtime"]["required_libraries"]:
        handle = api.GetModuleHandleW(name)
        buffer = ctypes.create_unicode_buffer(32768)
        if (not handle or not api.GetModuleFileNameW(handle, buffer, len(buffer))
                or Path(buffer.value).resolve() != root / name):
            raise RuntimeError("Native runtime did not load from the private directory.")
    report("cpu_backend", 1, 1)
    return []


def handle(job, report, canceled):
    if job.get("request", {}).get("operation") == "runtime.health":
        return health(job, report, canceled)
    if job.get("request", {}).get("operation") == "section_audio.synthesize":
        from app.runtime.section_worker import section_audio
        return section_audio(job, report, canceled)
    return diagnostic(job, report, canceled)


if __name__ == "__main__":
    # NumPy's native initialization can acquire CRT stdin locks. Import before
    # D007 starts its blocking cancel reader, otherwise Windows can deadlock.
    import numpy
    import onnxruntime
    import piper
    from piper import espeakbridge

    raise SystemExit(serve(handle))

"""Explicit real Piper download smoke. Never invoked by default pytest/CI."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.runtime.profiles import HostCapabilities
from app.runtime.provisioning import PiperProvisioner
from app.runtime.voice_download import PiperVoiceDownloader
from app.runtime.voice_http import DownloadInterrupted, VoiceHTTPTransport


class RecordingHTTP(VoiceHTTPTransport):
    def __init__(self):
        self.ranges = []

    @contextmanager
    def open(self, url, *, offset=0, etag=None, probe=False):
        with super().open(url, offset=offset, etag=etag, probe=probe) as response:
            if not probe:
                self.ranges.append({"offset": offset, "status": response.status,
                                    "range": response.headers.get("Content-Range")})
            yield response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New ignored smoke directory")
    parser.add_argument("--voice", default="pl_PL-gosia-medium")
    parser.add_argument("--language", default="pl_PL")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = PiperProvisioner(args.runtime_root, HostCapabilities("windows", "x86_64", ("cpu",)))
    transport = RecordingHTTP()
    service = PiperVoiceDownloader(output / "models", runtime, transport=transport)
    stop = False

    def progress(name, done, total):
        nonlocal stop
        if name.endswith(".onnx") and done >= 1024 * 1024:
            stop = True

    try:
        service.download(args.voice, language_id=args.language, canceled=lambda: stop, progress=progress)
    except DownloadInterrupted:
        pass
    else:
        raise RuntimeError("Expected a deliberate interrupted real download.")
    if service.installed(args.voice, language_id=args.language) is not None:
        raise RuntimeError("Interrupted download unexpectedly activated a voice.")
    partial = next((output / "models/downloads").glob("*/*.onnx.part"))
    prefix = partial.stat().st_size
    restarted = PiperVoiceDownloader(output / "models", runtime, transport=transport)
    voice = restarted.download(args.voice, language_id=args.language)
    if restarted.installed(args.voice, language_id=args.language) != voice:
        raise RuntimeError("Restart did not recognize the installed voice.")
    if not any(r["offset"] == prefix and r["status"] == 206 for r in transport.ranges):
        raise RuntimeError("Real server did not resume the interrupted prefix.")
    report = {"automated_pass": True, "voice": voice.provider_key, "language": voice.language_id,
              "fingerprint": voice.fingerprint, "interrupted_prefix_bytes": prefix,
              "requests": transport.ranges, "restart_same_version": True,
              "receipt": json.loads((voice.directory / "voice.json").read_text(encoding="utf-8"))}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "receipt"}, indent=2))


if __name__ == "__main__":
    main()

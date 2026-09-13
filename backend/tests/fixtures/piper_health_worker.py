"""Lightweight offline process for the private-runtime health supervisor."""

import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.runtime.protocol import encode_frame, message, read_message


case = sys.argv[1]
hello = read_message(sys.stdin.buffer)
job, attempt = hello["job_id"], hello["attempt_id"]


def send(kind, payload):
    sys.stdout.buffer.write(encode_frame(message(kind, job, attempt, payload)))
    sys.stdout.buffer.flush()


send("ready", {"pid": os.getpid() + (1 if case == "wrong-pid" else 0)})
read_message(sys.stdin.buffer)
if case == "hang":
    time.sleep(60)
if case == "flood":
    while True:
        os.write(sys.stdout.fileno(), b"x" * 8192)
if case == "stderr":
    os.write(sys.stderr.fileno(), b"x" * 262144)
for check in ("interpreter", "packages", "cpu_backend"):
    if case == "failed" and check == "cpu_backend":
        send("failed", {"code": "fixture_error", "message": "CPU unavailable"})
        raise SystemExit(0)
    send("progress", {"phase": check, "completed": 1, "total": 1})
send("completed", {"artifact_ids": []})
if case == "extra":
    send("completed", {"artifact_ids": []})
if case == "terminal-hang":
    time.sleep(60)
raise SystemExit(3 if case == "nonzero" else 0)

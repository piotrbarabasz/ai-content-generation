"""Fault-injection executable selected by tests, never by a user's job request."""

import json
import os
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.runtime.protocol import MAX_FRAME_BYTES, encode_frame, message, read_message


case = os.environ["WORKER_CASE"]
if case == "no-handshake":
    time.sleep(60)
hello = read_message(sys.stdin.buffer)
job_id, attempt_id = hello["job_id"], hello["attempt_id"]


def send(kind, payload=None):
    frame = encode_frame(message(kind, job_id, attempt_id, payload))
    if case == "fragmented":
        for byte in frame:
            sys.stdout.buffer.write(bytes([byte]))
            sys.stdout.buffer.flush()
    else:
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()


if case == "version":
    reply = message("ready", job_id, attempt_id, {"pid": os.getpid()})
    reply["version"] = 99
    body = json.dumps(reply).encode()
    sys.stdout.buffer.write(struct.pack(">I", len(body)) + body)
    sys.stdout.buffer.flush()
    time.sleep(60)
elif case == "oversized":
    sys.stdout.buffer.write(struct.pack(">I", MAX_FRAME_BYTES + 1))
    sys.stdout.buffer.flush()
    time.sleep(60)
elif case == "truncated":
    sys.stdout.buffer.write(b"\x00\x00\x00\x10{}")
    sys.stdout.buffer.flush()
    sys.exit(0)
elif case == "malformed":
    sys.stdout.buffer.write(b"\x00\x00\x00\x01{")
    sys.stdout.buffer.flush()
    sys.exit(0)
elif case == "stdout-log":
    print("unexpected log on stdout", flush=True)
    sys.exit(0)
elif case == "wrong-id":
    attempt_id = "foreign_attempt"
send("ready", {"pid": os.getpid() + 1 if case == "wrong-pid" else os.getpid()})
run = read_message(sys.stdin.buffer)
assert run["type"] == "run" and run["payload"]["job"]["input_snapshot"]["text"] == "B1"
print("diagnostic stderr", file=sys.stderr, flush=True)
send("progress", {"phase": "work", "completed": 1, "total": 2})
if case in ("hang", "ignore-cancel"):
    time.sleep(60)
elif case == "flood":
    while True:
        send("progress", {"phase": "work", "completed": 1, "total": 2})
elif case == "tree":
    import subprocess
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(f"grandchild={child.pid}", file=sys.stderr, flush=True)
    time.sleep(60)
elif case in ("cancel", "late-complete"):
    assert read_message(sys.stdin.buffer)["type"] == "cancel"
    send("canceled" if case == "cancel" else "completed", None if case == "cancel" else {"artifact_ids": ["late_result"]})
elif case == "failure":
    send("failed", {"code": "fixture_failure", "message": "offline failure"})
elif case == "regression":
    send("progress", {"phase": "work", "completed": 0, "total": 2})
elif case == "unsolicited-cancel":
    send("canceled")
elif case == "no-outcome":
    pass
else:
    if case == "stderr-flood":
        sys.stderr.write("x" * 300000 + "LOG-END")
        sys.stderr.flush()
    send("completed", {"artifact_ids": ["reported_artifact"]})
    if case == "extra":
        send("completed", {"artifact_ids": []})
    elif case == "terminal-hang":
        time.sleep(60)
    elif case == "nonzero":
        sys.exit(7)

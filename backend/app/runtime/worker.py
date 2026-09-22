"""One-job diagnostic worker and cooperative handler boundary; no real models."""

import os
import sys
import threading
from contextlib import nullcontext, redirect_stdout

from .protocol import ProtocolError, check_identity, encode_frame, message, read_message


def diagnostic(job, report, canceled):
    if job.get("request", {}).get("operation") != "diagnostic.echo":
        raise ValueError("Unsupported diagnostic operation.")
    report("diagnostic", 1, 1)
    return []  # IPC smoke produces no media and never invents artifact references.


class WorkerFailure(RuntimeError):
    """Trusted worker composition can preserve a bounded actionable failure code."""

    def __init__(self, code, message):
        if code != "gpu_oom":
            raise ValueError("Unsupported actionable worker failure.")
        self.code = code
        super().__init__(message)


def serve(handler=diagnostic, *, quiet_handler=False, initialize=None):
    # Unbuffered handles avoid shutdown locks from the daemon cancel reader.
    incoming = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    outgoing = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    if quiet_handler:
        # Capture C/native writes too, while protocol frames use the owned duplicate.
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        if initialize is not None:
            initialize()  # Before the blocking cancel reader (native CRT stdin locks).
        hello = read_message(incoming)
        if hello is None or hello["type"] != "hello":
            raise ProtocolError("Expected version handshake.")
        job_id, attempt_id = hello["job_id"], hello["attempt_id"]

        def send(kind, payload=None):
            frame = encode_frame(message(kind, job_id, attempt_id, payload))
            view = memoryview(frame)
            while view:
                written = outgoing.write(view)
                if not written:
                    raise BrokenPipeError("Parent pipe closed.")
                view = view[written:]

        send("ready", {"pid": os.getpid()})
        run = check_identity(read_message(incoming), job_id, attempt_id)
        if run["type"] != "run":
            raise ProtocolError("Expected run after handshake.")
        canceled = threading.Event()
        bad_command = threading.Event()

        def listen():
            try:
                command = check_identity(read_message(incoming), job_id, attempt_id)
                if command["type"] != "cancel":
                    raise ProtocolError("Only cancel is accepted during execution.")
            except (ValueError, OSError):
                bad_command.set()
            finally:
                canceled.set()

        threading.Thread(target=listen, daemon=True, name="worker-cancel").start()

        def report(phase, completed=None, total=None):
            send("progress", {"phase": phase, "completed": completed, "total": total})

        try:
            with redirect_stdout(sys.stderr) if quiet_handler else nullcontext():
                outputs = handler(run["payload"]["job"], report, canceled)
            if bad_command.is_set():
                raise ProtocolError("Parent disconnected or sent an invalid command.")
            send("canceled" if canceled.is_set() else "completed",
                 None if canceled.is_set() else {"artifact_ids": outputs})
        except Exception as exc:
            print(f"Worker handler failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            send("failed", {"code": exc.code if isinstance(exc, WorkerFailure) else "handler_error",
                            "message": str(exc)[:4096] or type(exc).__name__})
        return 0
    except (ValueError, OSError) as exc:
        print(f"Worker protocol failed: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 2
    finally:
        if quiet_handler:
            sys.stdout.flush()
            os.dup2(outgoing.fileno(), sys.stdout.fileno())
        outgoing.close()
        # Incoming may be owned by the cancel reader; the process owns its lifetime.


if __name__ == "__main__":
    raise SystemExit(serve())

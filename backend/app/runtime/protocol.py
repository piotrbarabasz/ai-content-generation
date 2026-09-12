"""Version 1: length-prefixed UTF-8 JSON over private binary pipes, never pickle."""

import asyncio
import json
import math
import re
import struct


VERSION = 1
MAX_FRAME_BYTES = 256 * 1024
MAX_ARTIFACTS = 256
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class ProtocolError(ValueError):
    pass


def _text(value, limit=4096):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def validate(message):
    if not isinstance(message, dict) or set(message) != {"version", "type", "job_id", "attempt_id", "payload"}:
        raise ProtocolError("Invalid worker envelope.")
    if type(message["version"]) is not int or message["version"] != VERSION:
        raise ProtocolError("Unsupported worker protocol version.")
    if not _identifier(message["job_id"]) or not _identifier(message["attempt_id"]):
        raise ProtocolError("Invalid job/attempt identity.")
    kind, data = message["type"], message["payload"]
    if not isinstance(data, dict):
        raise ProtocolError("Worker payload must be an object.")
    valid = False
    if kind in ("hello", "cancel", "canceled"):
        valid = data == {}
    elif kind == "ready":
        valid = set(data) == {"pid"} and type(data["pid"]) is int and data["pid"] > 0
    elif kind == "run":
        valid = set(data) == {"job"} and isinstance(data["job"], dict) and data["job"].get("id") == message["job_id"]
    elif kind == "progress":
        valid = set(data) == {"phase", "completed", "total"} and _text(data["phase"], 128)
        for value in (data.get("completed"), data.get("total")):
            valid = valid and (value is None or type(value) is int and 0 <= value <= 2**63 - 1)
        if data.get("total") is not None:
            valid = valid and data.get("completed") is not None and data["completed"] <= data["total"]
    elif kind == "completed":
        ids = data.get("artifact_ids")
        valid = (set(data) == {"artifact_ids"} and isinstance(ids, list) and len(ids) <= MAX_ARTIFACTS
                 and all(_identifier(value) for value in ids) and len(set(ids)) == len(ids))
    elif kind == "failed":
        valid = set(data) == {"code", "message"} and _identifier(data.get("code")) and _text(data.get("message"))
    if not valid:
        raise ProtocolError("Invalid worker message type or payload.")
    return message


def message(kind, job_id, attempt_id, payload=None):
    return validate({"version": VERSION, "type": kind, "job_id": job_id,
                     "attempt_id": attempt_id, "payload": {} if payload is None else payload})


def encode_frame(value):
    validate(value)
    try:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("Worker message is not strict UTF-8 JSON.") from exc
    if not 0 < len(body) <= MAX_FRAME_BYTES:
        raise ProtocolError("Worker frame exceeds the size limit.")
    return struct.pack(">I", len(body)) + body


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key.")
        result[key] = value
    return result


def _constant(_value):
    raise ProtocolError("Non-finite JSON number.")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError("Non-finite JSON number.")
    return result


def decode_body(body):
    if not 0 < len(body) <= MAX_FRAME_BYTES:
        raise ProtocolError("Invalid worker frame size.")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
        return validate(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("Invalid worker JSON or envelope.") from exc


def frame_size(header):
    if len(header) != 4:
        raise ProtocolError("Truncated worker frame header.")
    size = struct.unpack(">I", header)[0]
    if not 0 < size <= MAX_FRAME_BYTES:
        raise ProtocolError("Invalid worker frame size.")
    return size


def read_message(stream):
    def exact(size, *, allow_eof=False):
        data = bytearray()
        while len(data) < size:
            chunk = stream.read(size - len(data))
            if not chunk:
                if not data and allow_eof:
                    return None
                raise ProtocolError("Truncated worker frame.")
            data.extend(chunk)
        return bytes(data)
    header = exact(4, allow_eof=True)
    return None if header is None else decode_body(exact(frame_size(header)))


async def read_message_async(reader):
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ProtocolError("Truncated worker frame header.") from exc
    try:
        return decode_body(await reader.readexactly(frame_size(header)))
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("Truncated worker frame body.") from exc


def check_identity(value, job_id, attempt_id):
    if value is None or (value["job_id"], value["attempt_id"]) != (job_id, attempt_id):
        raise ProtocolError("Worker EOF or mismatched job/attempt identity.")
    return value

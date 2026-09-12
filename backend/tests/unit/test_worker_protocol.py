import asyncio
import io
import json
import struct

import pytest

from app.runtime.protocol import (
    MAX_FRAME_BYTES, ProtocolError, decode_body, encode_frame, message, read_message, read_message_async,
)


def test_sync_and_async_framing_round_trip_unicode_and_eof():
    value = message("failed", "job_1", "attempt_1", {"code": "test", "message": "Zażółć gęślą jaźń"})
    frame = encode_frame(value)
    stream = io.BytesIO(frame)
    assert read_message(stream) == value and read_message(stream) is None
    async def check():
        reader = asyncio.StreamReader()
        reader.feed_data(frame[:2])
        reader.feed_data(frame[2:])
        reader.feed_eof()
        assert await read_message_async(reader) == value
        assert await read_message_async(reader) is None
    asyncio.run(check())


@pytest.mark.parametrize("body", [b"{}", b"[]", b"null", b"{", b"\xff", b'{"x":NaN}',
                                 b'{"x":1e999}', b'{"x":1,"x":2}', b'[' * 1100])
def test_invalid_json_is_rejected(body):
    with pytest.raises(ProtocolError):
        decode_body(body)


@pytest.mark.parametrize("frame", [b"\x00", b"\x00\x00\x00\x00", struct.pack(">I", MAX_FRAME_BYTES + 1), b"\x00\x00\x00\x08{}"])
def test_bounded_truncated_frames_fail_without_waiting_for_unbounded_body(frame):
    with pytest.raises(ProtocolError):
        read_message(io.BytesIO(frame))
    async def check():
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        with pytest.raises(ProtocolError):
            await read_message_async(reader)
    asyncio.run(check())


@pytest.mark.parametrize("field,value", [("version", True), ("version", 2), ("job_id", "../../file"),
                                         ("attempt_id", ""), ("type", "exec"), ("payload", [])])
def test_unknown_version_identity_and_message_types_are_rejected(field, value):
    envelope = message("hello", "job_1", "attempt_1")
    envelope[field] = value
    with pytest.raises(ProtocolError):
        decode_body(json.dumps(envelope).encode())


def test_outgoing_request_limit_and_nonfinite_values():
    for value in ("x" * MAX_FRAME_BYTES, float("nan")):
        envelope = message("run", "job_1", "attempt_1", {"job": {"id": "job_1", "input": value}})
        with pytest.raises(ProtocolError):
            encode_frame(envelope)

"""Deterministic valid PNG fixtures; no model, filesystem, Pillow or network."""

import hashlib
import struct
import zlib

from .image_generation import ImageGenerationCapabilities, ImageGenerationResult


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


class MockImageProvider:
    def capabilities(self):
        return ImageGenerationCapabilities("mock", "rgb-pattern", "1")

    def generate(self, request):
        self.capabilities().validate(request)
        colors = hashlib.sha256(request.fingerprint.encode("ascii")).digest()
        rows = []
        for y in range(request.height):
            rows.append(b"\x00" + b"".join(colors[3 * ((x // 8 + y // 8) % 2):3 * ((x // 8 + y // 8) % 2) + 3]
                                           for x in range(request.width)))
        payload = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", request.width, request.height, 8, 2, 0, 0, 0))
                   + _chunk(b"IDAT", zlib.compress(b"".join(rows))) + _chunk(b"IEND", b""))
        return ImageGenerationResult(payload, "PNG", request.width, request.height)

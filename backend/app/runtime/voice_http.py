"""Bounded HTTP range transport for curated Piper assets; no provider imports."""

from contextlib import contextmanager
from dataclasses import dataclass
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class VoiceDownloadError(ValueError):
    pass


class DownloadInterrupted(VoiceDownloadError):
    """The verified publication is unchanged; partial bytes can be resumed."""


@dataclass(frozen=True)
class RemoteAsset:
    size: int
    etag: str | None


def _https(url, *, origin=False):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    allowed = host == "huggingface.co" if origin else (
        host == "huggingface.co" or host.endswith((".huggingface.co", ".hf.co")))
    if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment):
        raise VoiceDownloadError("Voice transport requires an approved HTTPS publisher/CDN.")


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class VoiceHTTPTransport:
    """Explicit network adapter. Each response is closed by its context manager."""

    @contextmanager
    def open(self, url, *, offset=0, etag=None, probe=False):
        _https(url, origin=True)
        headers = {"Accept-Encoding": "identity", "User-Agent": "AIContentStudio-D009",
                   "Range": "bytes=0-0" if probe else f"bytes={offset}-"}
        if etag and offset:
            headers["If-Range"] = etag
        try:
            response = build_opener(_Redirects()).open(Request(url, headers=headers), timeout=30)
        except HTTPError as exc:
            if exc.code != 416:
                exc.close()
                raise VoiceDownloadError(f"Voice publisher returned HTTP {exc.code}.") from None
            response = exc
        except (URLError, OSError):
            raise DownloadInterrupted("Voice connection failed; retry to resume.") from None
        with response:
            yield response


def response_extent(response):
    """Validate identity encoding and exact byte extents before modifying a partial."""
    headers = {key.lower(): value for key, value in response.headers.items()}
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise VoiceDownloadError("Encoded responses cannot be combined as byte ranges.")
    length = headers.get("content-length", "")
    if not re.fullmatch(r"[0-9]{1,12}", length) or int(length) <= 0:
        raise VoiceDownloadError("Voice response requires a bounded Content-Length.")
    length = int(length)
    if response.status == 200:
        if "content-range" in headers:
            raise VoiceDownloadError("Unexpected Content-Range on a full response.")
        return 0, length - 1, length
    if response.status != 206:
        raise VoiceDownloadError("Voice server did not supply a usable full/range response.")
    match = re.fullmatch(r"bytes ([0-9]{1,12})-([0-9]{1,12})/([0-9]{1,12})", headers.get("content-range", ""))
    if not match:
        raise VoiceDownloadError("Invalid voice Content-Range.")
    start, end, total = map(int, match.groups())
    if not 0 <= start <= end < total or end - start + 1 != length:
        raise VoiceDownloadError("Inconsistent voice response extent.")
    return start, end, total


def inspect_remote(transport, url, limit):
    with transport.open(url, probe=True) as response:
        start, _, total = response_extent(response)
        if start != 0 or total > limit:
            raise VoiceDownloadError("Voice asset exceeds its permitted size or probe range.")
        etag = response.headers.get("ETag")
        if not isinstance(etag, str) or not re.fullmatch(r'"[^"\r\n]{1,256}"', etag):
            etag = None
        return RemoteAsset(total, etag)

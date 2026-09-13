"""Offline wire-contract tests; no external HTTP server or provider connection."""

import io
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from app.runtime import voice_http as http


@pytest.mark.parametrize("status,headers", [
    (206, {"Content-Length": "1", "Content-Range": "bytes 1-0/2"}),
    (206, {"Content-Length": "2", "Content-Range": "bytes 0-0/2"}),
    (206, {"Content-Length": "1", "Content-Range": "bytes 0-0/*"}),
    (206, {"Content-Length": "1", "Content-Range": "bytes 0-0/0"}),
    (200, {"Content-Length": "1", "Content-Range": "bytes 0-0/1"}),
    (200, {"Content-Length": "-1"}), (200, {"Content-Length": "0"}),
    (200, {"Content-Length": "99999999999999999999999"}),
    (200, {"Content-Length": "1", "Content-Encoding": "br"}),
    (302, {"Content-Length": "1"}),
])
def test_invalid_wire_extent_is_rejected(status, headers):
    with pytest.raises(http.VoiceDownloadError):
        http.response_extent(SimpleNamespace(status=status, headers=headers))


def test_valid_full_and_partial_extents_accept_case_insensitive_headers():
    assert http.response_extent(SimpleNamespace(status=200, headers={"content-length": "12"})) == (0, 11, 12)
    assert http.response_extent(SimpleNamespace(status=206, headers={
        "Content-Length": "4", "Content-Range": "bytes 8-11/12"})) == (8, 11, 12)


@pytest.mark.parametrize("url", ["http://huggingface.co/voice", "file:///model", "https://evil.example/model",
                                "https://huggingface.co.evil.example/model", "https://user:secret@huggingface.co/model",
                                "https://huggingface.co:8443/model"])
def test_unapproved_origin_is_rejected_before_opener(url, monkeypatch):
    monkeypatch.setattr(http, "build_opener", lambda *_: pytest.fail("Unexpected HTTP access"))
    with pytest.raises(http.VoiceDownloadError):
        with http.VoiceHTTPTransport().open(url):
            pass


def test_redirects_allow_only_publisher_https_cdn():
    http._https("https://cas-bridge.xethub.hf.co/public-artifact?Signature=fixture")
    with pytest.raises(http.VoiceDownloadError):
        http._https("http://cas-bridge.xethub.hf.co/file")
    with pytest.raises(http.VoiceDownloadError):
        http._https("https://arbitrary.example/file")


def test_transport_sets_range_validator_and_closes_response(monkeypatch):
    response = io.BytesIO(b"fixture")
    seen = []
    def open_request(request, timeout):
        seen.append((request, timeout))
        return response
    monkeypatch.setattr(http, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    with http.VoiceHTTPTransport().open("https://huggingface.co/voice", offset=4096, etag='"revision"') as stream:
        assert stream.read() == b"fixture"
    request, timeout = seen[0]
    assert request.get_header("Range") == "bytes=4096-"
    assert request.get_header("If-range") == '"revision"'
    assert request.get_header("Accept-encoding") == "identity"
    assert timeout == 30 and response.closed


@pytest.mark.parametrize("code", [404, 500])
def test_http_errors_close_body_and_do_not_expose_redirect_url(monkeypatch, code):
    body = io.BytesIO(b"error")
    def open_request(*args, **kwargs):
        raise HTTPError("https://cdn.example?secret=fixture", code, "error", {}, body)
    monkeypatch.setattr(http, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    with pytest.raises(http.VoiceDownloadError, match=f"HTTP {code}") as error:
        with http.VoiceHTTPTransport().open("https://huggingface.co/model"):
            pass
    assert "secret" not in str(error.value) and body.closed


def test_connection_failure_is_resumable(monkeypatch):
    def open_request(*args, **kwargs):
        raise URLError("fixture")
    monkeypatch.setattr(http, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    with pytest.raises(http.DownloadInterrupted):
        with http.VoiceHTTPTransport().open("https://huggingface.co/model"):
            pass

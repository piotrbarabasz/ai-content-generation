from __future__ import annotations

import asyncio
import builtins
import json
import sys
from urllib.parse import urlencode

import pytest

from app.api.main import create_app


_OPTIONAL_TTS_RUNTIME_ROOTS = frozenset(
    {
        "TTS",
        "chatterbox",
        "piper",
        "torch",
        "torchaudio",
    }
)


def _collect_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                refs.add(child)
            refs.update(_collect_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_collect_refs(child))
    return refs


async def _call_get(app, path: str, query_string: bytes = b"") -> tuple[int, dict[str, str], bytes]:
    messages: list[dict[str, object]] = []
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    await app(scope, receive, send)

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = {
        key.decode("latin1"): value.decode("latin1")
        for key, value in start["headers"]  # type: ignore[index]
    }
    return start["status"], headers, body  # type: ignore[index]


def test_tts_catalog_endpoint_exposes_neutral_openapi_and_http_behavior() -> None:
    app = create_app()
    openapi = app.openapi()

    schemas = openapi["components"]["schemas"]
    public_tts_components = {
        "TTSCapabilities",
        "TTSCatalog",
        "TTSModel",
        "TTSProvider",
        "TTSVoice",
    }
    tts_components = {name for name in schemas if name.startswith("TTS")}
    assert tts_components == public_tts_components
    assert not any(
        name.endswith(("DescriptorSchema", "Response", "Schema"))
        for name in tts_components
    )
    assert schemas["TTSCatalog"]["title"] == "TTS Catalog"
    assert schemas["TTSProvider"]["title"] == "TTS Provider"
    assert schemas["TTSModel"]["title"] == "TTS Model"
    assert schemas["TTSVoice"]["title"] == "TTS Voice"
    assert schemas["TTSCapabilities"]["title"] == "TTS Capabilities"

    tts_refs = {
        ref
        for ref in _collect_refs(openapi)
        if ref.startswith("#/components/schemas/TTS")
    }
    assert tts_refs == {
        f"#/components/schemas/{name}" for name in public_tts_components
    }
    assert (
        openapi["paths"]["/api/v1/tts/catalog"]["get"]["responses"]["200"]
        ["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/TTSCatalog"
    )

    status, headers, body = asyncio.run(_call_get(app, "/api/v1/tts/catalog"))
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    payload = json.loads(body)
    assert tuple(provider["id"] for provider in payload["providers"]) == (
        "chatterbox_v3",
        "piper",
        "xtts_v2_eval",
    )
    assert set(payload["providers"][0]) == {
        "id",
        "displayName",
        "usagePolicy",
        "supportedLanguages",
        "capabilities",
        "models",
    }

    filtered_query = urlencode({"language": "pl", "usagePolicy": "production"}).encode("ascii")
    filtered_status, filtered_headers, filtered_body = asyncio.run(
        _call_get(app, "/api/v1/tts/catalog", filtered_query)
    )
    assert filtered_status == 200
    assert filtered_headers["content-type"].startswith("application/json")
    filtered_payload = json.loads(filtered_body)
    assert tuple(provider["id"] for provider in filtered_payload["providers"]) == (
        "chatterbox_v3",
        "piper",
    )
    assert filtered_payload == json.loads(
        asyncio.run(_call_get(app, "/api/v1/tts/catalog", filtered_query))[2]
    )

    empty_status, _, empty_body = asyncio.run(
        _call_get(app, "/api/v1/tts/catalog", urlencode({"language": "de", "usagePolicy": "production"}).encode("ascii"))
    )
    assert empty_status == 200
    assert json.loads(empty_body)["providers"] == []

    invalid_status, invalid_headers, invalid_body = asyncio.run(
        _call_get(app, "/api/v1/tts/catalog", urlencode({"language": "bad tag"}).encode("ascii"))
    )
    assert invalid_status == 400
    assert invalid_headers["content-type"].startswith("application/json")
    assert "normalized language tag" in json.loads(invalid_body)["detail"]

    invalid_usage_status, invalid_usage_headers, invalid_usage_body = asyncio.run(
        _call_get(app, "/api/v1/tts/catalog", urlencode({"usagePolicy": "experimental"}).encode("ascii"))
    )
    assert invalid_usage_status == 400
    assert invalid_usage_headers["content-type"].startswith("application/json")
    assert "usage_policy" in json.loads(invalid_usage_body)["detail"]


def test_tts_catalog_http_request_does_not_import_optional_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for module_name in tuple(sys.modules):
        if module_name.split(".", 1)[0] in _OPTIONAL_TTS_RUNTIME_ROOTS:
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    original_import = builtins.__import__
    attempted_runtime_imports: list[str] = []

    def guarded_import(
        name: str,
        globals=None,
        locals=None,
        fromlist=(),
        level: int = 0,
    ):
        if level == 0 and name.split(".", 1)[0] in _OPTIONAL_TTS_RUNTIME_ROOTS:
            attempted_runtime_imports.append(name)
            raise AssertionError(f"catalog endpoint attempted optional runtime import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    status, _, body = asyncio.run(_call_get(create_app(), "/api/v1/tts/catalog"))

    assert status == 200
    assert json.loads(body)["providers"]
    assert attempted_runtime_imports == []
    assert not {
        module_name
        for module_name in sys.modules
        if module_name.split(".", 1)[0] in _OPTIONAL_TTS_RUNTIME_ROOTS
    }

"""D026 OpenAI adapter settings, transport, throttling and redaction."""

from __future__ import annotations

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.domain.script_sections import script_sections_schema
from app.providers.llm_factory import LLMFactoryError, build_llm_provider
from app.providers.llm_settings import LLMSettingsError, OpenAISettings
from app.providers.openai_llm import (
    OpenAILLMError,
    OpenAILLMProvider,
    OpenAITransportError,
    UrllibOpenAITransport,
)
from app.providers.registry import ProviderRegistry


def response(text: str, *, model="gpt-test", status="completed"):
    return {
        "id": "resp_123",
        "model": model,
        "status": status,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    }


class Transport:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def create_response(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def provider(transport, **settings):
    values = {"model": "gpt-test", **settings}
    return OpenAILLMProvider(OpenAISettings.from_mapping(values), transport=transport,
                             environment={"OPENAI_API_KEY": "sk-private-fixture"})


def test_structured_response_uses_strict_responses_format_and_records_safe_usage():
    transport = Transport(response('{"sections":[{"title":"A","role":"hook","text":"Body"}]}'))
    llm = provider(transport)
    result = llm.generate_structured("Write it", script_sections_schema())
    assert result["sections"][0]["text"] == "Body"
    call = transport.calls[0]
    assert call["api_key"] == "sk-private-fixture" and call["timeout_seconds"] == 60
    assert call["payload"]["text"]["format"] == {
        "type": "json_schema",
        "name": "urn_aics_script-sections_v1",
        "strict": True,
        "schema": {key: value for key, value in script_sections_schema().items() if key != "$id"},
    }
    assert call["payload"]["store"] is False
    assert "sk-private-fixture" not in json.dumps(call["payload"])
    assert llm.last_usage() == {
        "requestId": "resp_123", "model": "gpt-test", "inputTokens": 11,
        "outputTokens": 7, "totalTokens": 18,
    }
    assert llm.generation_identity() == {"provider": "openai", "api": "responses", "model": "gpt-test"}


def test_text_generation_serializes_context_and_rejects_missing_credentials():
    transport = Transport(response("Generated text"))
    llm = provider(transport)
    assert llm.generate_text("Prompt", {"language": "pl"}) == "Generated text"
    assert json.loads(transport.calls[0]["payload"]["input"]) == {
        "prompt": "Prompt", "context": {"language": "pl"}
    }
    missing = OpenAILLMProvider(OpenAISettings.from_mapping({"model": "gpt-test"}),
                                transport=Transport(response("unused")), environment={})
    with pytest.raises(OpenAILLMError, match="OPENAI_API_KEY"):
        missing.generate_text("Prompt")


def test_retryable_rate_limit_and_request_interval_use_injected_clock():
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    transport = Transport(
        OpenAITransportError("rate limited", status_code=429, retryable=True, retry_after=3),
        response("first"), response("second"),
    )
    llm = OpenAILLMProvider(
        OpenAISettings.from_mapping({"model": "gpt-test", "maxRetries": 1,
                                     "requestIntervalSeconds": 2, "retryDelaySeconds": 1}),
        transport=transport, environment={"OPENAI_API_KEY": "secret"},
        monotonic=lambda: clock[0], sleep=sleep,
    )
    assert llm.generate_text("one") == "first"
    assert llm.generate_text("two") == "second"
    assert sleeps == [3, 2]
    assert len(transport.calls) == 3


@pytest.mark.parametrize("payload, message", [
    ({"status": "incomplete", "output": []}, "did not complete"),
    ({"status": "completed", "output": []}, "no output text"),
    ({"status": "completed", "output": [{"type": "message", "content": [
        {"type": "refusal", "refusal": "no"}
    ]}]}, "refused"),
])
def test_incomplete_empty_and_refused_responses_fail_closed(payload, message):
    with pytest.raises(OpenAILLMError, match=message):
        provider(Transport(payload)).generate_structured("Prompt", {"type": "object"})


def test_malformed_structured_json_is_rejected():
    with pytest.raises(OpenAILLMError, match="valid JSON"):
        provider(Transport(response("not json"))).generate_structured("Prompt", {"type": "object"})
    with pytest.raises(OpenAILLMError, match="JSON object"):
        provider(Transport(response("[]"))).generate_structured("Prompt", {"type": "object"})


def test_settings_and_http_errors_never_serialize_credentials():
    for secret_setting in ({"model": "gpt-test", "apiKey": "secret"},
                           {"model": "gpt-test", "token": "secret"}):
        with pytest.raises(LLMSettingsError, match="environment variable") as captured:
            OpenAISettings.from_mapping(secret_setting)
        assert "secret" not in str(captured.value)

    def opener(*args, **kwargs):
        del args, kwargs
        raise HTTPError("https://api.openai.com/v1/responses?key=secret", 401, "secret", {},
                        BytesIO(b'{"error":"sk-private-fixture"}'))

    transport = UrllibOpenAITransport(opener=opener)
    with pytest.raises(OpenAITransportError, match="authentication") as captured:
        transport.create_response(payload={"model": "x"}, api_key="sk-private-fixture", timeout_seconds=1)
    assert "private" not in str(captured.value) and "key=" not in str(captured.value)


def test_factory_selects_only_mock_or_openai_and_registers_protocol():
    config = ProviderConfig.create(workflow_config_id="workflow", provider_type=ProviderType.LLM,
                                   provider_name="openai", settings={"model": "gpt-test"})
    registry = ProviderRegistry()
    built = build_llm_provider(config, registry=registry, transport=Transport(response("ok")),
                               environment={"OPENAI_API_KEY": "secret"})
    assert registry.resolve_from_config(config) is built
    wrong = ProviderConfig.create(workflow_config_id="workflow", provider_type=ProviderType.LLM,
                                  provider_name="another", settings={})
    with pytest.raises(LLMFactoryError, match="Unsupported"):
        build_llm_provider(wrong)

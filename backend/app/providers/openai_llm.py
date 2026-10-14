"""OpenAI Responses API adapter for the provider-neutral LLM contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
import re
import threading
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.domain.enums import ProviderType
from app.domain.types import JsonDict
from app.providers.llm_settings import OpenAISettings
from app.domain.llm_errors import StructuredOutputError, LLMRequestCanceled


RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"


class OpenAILLMError(RuntimeError):
    """Credential-safe OpenAI provider failure."""
    category = "CONFIGURATION_OR_PROVIDER_REJECTION"


class OpenAIStructuredOutputError(OpenAILLMError, StructuredOutputError):
    category = "SECTION_CONTRACT"


class OpenAITransportError(OpenAILLMError):
    def __init__(self, message: str, *, status_code: int | None = None,
                 retryable: bool = False, retry_after: float | None = None,
                 category: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after
        self.category = category or ("RATE_LIMIT" if status_code == 429 else
                         "TRANSIENT_PROVIDER" if status_code and status_code >= 500 else
                         "CONFIGURATION" if status_code else "NETWORK_TIMEOUT")


class OpenAITransport(Protocol):
    def create_response(self, *, payload: JsonDict, api_key: str,
                        timeout_seconds: float) -> Mapping[str, Any]: ...


class UrllibOpenAITransport:
    """Small dependency-free HTTPS transport; response bodies never enter errors."""

    def __init__(self, *, opener: Callable[..., Any] = urlopen) -> None:
        self._opener = opener

    def create_response(self, *, payload: JsonDict, api_key: str,
                        timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(
            RESPONSES_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            status = int(exc.code)
            retry_after = None
            try:
                retry_after = float(exc.headers.get("Retry-After"))
            except (AttributeError, TypeError, ValueError):
                pass
            if status in (401, 403):
                message = "OpenAI authentication failed."
            elif status == 429:
                message = "OpenAI request was rate limited."
            elif 400 <= status < 500:
                message = "OpenAI rejected the request."
            else:
                message = "OpenAI service is unavailable."
            raise OpenAITransportError(message, status_code=status,
                                       retryable=status == 429 or status >= 500,
                                       retry_after=retry_after) from None
        except (TimeoutError, URLError, OSError) as exc:
            timeout = isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError))
            message = "OpenAI request timed out." if timeout else "OpenAI network request failed."
            raise OpenAITransportError(message, retryable=True,
                                       category="NETWORK_TIMEOUT" if timeout else "NETWORK_FAILURE") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpenAITransportError("OpenAI returned an invalid response.", category="TRANSIENT_PROVIDER") from None
        if not isinstance(result, Mapping):
            raise OpenAITransportError("OpenAI returned an invalid response.", category="TRANSIENT_PROVIDER")
        return result


@dataclass(frozen=True, slots=True)
class LLMUsage:
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def to_payload(self) -> JsonDict:
        return {
            "requestId": self.request_id,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
        }


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _unique_structured_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OpenAIStructuredOutputError("OpenAI structured output contained duplicate object fields.")
        result[key] = value
    return result


class OpenAILLMProvider:
    provider_type = ProviderType.LLM
    provider_name = "openai"

    def __init__(self, settings: OpenAISettings, *, transport: OpenAITransport | None = None,
                 environment: Mapping[str, str] | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self._transport = transport or UrllibOpenAITransport()
        self._environment = os.environ if environment is None else environment
        self._monotonic, self._sleep = monotonic, sleep
        self._throttle_lock = threading.Lock()
        self._last_request_at: float | None = None
        self._last_usage: LLMUsage | None = None
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def reset_cancel(self):
        self._cancel_event.clear()

    def _check_cancel(self):
        if self._cancel_event.is_set():
            raise LLMRequestCanceled("OpenAI request canceled; an in-flight request may still have been billed.")

    def _wait(self, delay):
        if self._sleep is time.sleep:
            self._cancel_event.wait(delay)
        else:
            self._sleep(delay)
        self._check_cancel()

    def generation_identity(self) -> JsonDict:
        return {"provider": self.provider_name, "api": "responses", "model": self.settings.model}

    def last_usage(self) -> JsonDict | None:
        return self._last_usage.to_payload() if self._last_usage else None

    def generate_text(self, prompt: str, context: JsonDict | None = None) -> str:
        self.reset_cancel()
        if not isinstance(prompt, str) or not prompt.strip():
            raise OpenAILLMError("OpenAI prompt must be nonempty text.")
        if context is not None and not isinstance(context, Mapping):
            raise OpenAILLMError("OpenAI text context must be an object.")
        input_text = prompt
        if context:
            input_text = json.dumps({"prompt": prompt, "context": dict(context)}, ensure_ascii=False)
        response = self._request({"model": self.settings.model, "input": input_text,
                                  "max_output_tokens": self.settings.max_output_tokens,
                                  "store": False})
        return self._output_text(response)

    def generate_structured(self, prompt: str, schema: JsonDict) -> JsonDict:
        self.reset_cancel()
        if not isinstance(prompt, str) or not prompt.strip():
            raise OpenAILLMError("OpenAI prompt must be nonempty text.")
        if type(schema) is not dict:
            raise OpenAILLMError("OpenAI structured-output schema must be an object.")
        try:
            frozen_schema = json.loads(json.dumps(schema, ensure_ascii=False))
        except (TypeError, ValueError):
            raise OpenAILLMError("OpenAI structured-output schema must be JSON-compatible.") from None
        schema_name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(schema.get("$id") or "aics_response"))[-64:]
        # `$id` identifies our local contract but is not part of the Structured
        # Outputs schema subset sent to the API. Downstream domain validation
        # still uses the untouched local schema and exact returned object.
        api_schema = {key: value for key, value in frozen_schema.items() if key != "$id"}
        payload = {
            "model": self.settings.model,
            "input": [
                {"role": "system", "content": "Return only data matching the supplied JSON schema."},
                {"role": "user", "content": prompt},
            ],
            "text": {"format": {"type": "json_schema", "name": schema_name,
                                  "strict": True, "schema": api_schema}},
            "max_output_tokens": self.settings.max_output_tokens,
            "store": False,
        }
        text = self._output_text(self._request(payload))
        try:
            result = json.loads(text, object_pairs_hook=_unique_structured_fields)
        except json.JSONDecodeError:
            raise OpenAIStructuredOutputError("OpenAI structured output was not valid JSON.") from None
        if type(result) is not dict:
            raise OpenAIStructuredOutputError("OpenAI structured output must be a JSON object.")
        return result

    def _api_key(self) -> str:
        value = self._environment.get(self.settings.api_key_env, "")
        if not isinstance(value, str) or not value.strip():
            raise OpenAILLMError(
                f"OpenAI credentials are unavailable; set {self.settings.api_key_env}."
            )
        return value.strip()

    def _throttle(self) -> None:
        with self._throttle_lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                remaining = self.settings.request_interval_seconds - (now - self._last_request_at)
                if remaining > 0:
                    self._wait(remaining)
                    now = self._monotonic()
            self._last_request_at = now

    def _request(self, payload: JsonDict) -> Mapping[str, Any]:
        api_key = self._api_key()
        for attempt in range(self.settings.max_retries + 1):
            self._check_cancel()
            self._throttle()
            try:
                response = self._transport.create_response(
                    payload=payload, api_key=api_key, timeout_seconds=self.settings.timeout_seconds
                )
                self._record_usage(response)
                return response
            except OpenAITransportError as exc:
                exc.attempts = attempt + 1
                logging.getLogger("aics.pipeline").warning(
                    "[AICS][LLM][REQUEST] category=%s attempt=%d retryable=%s billing=timeout_may_be_billed",
                    exc.category, attempt + 1, exc.retryable and attempt < self.settings.max_retries)
                if not exc.retryable or attempt >= self.settings.max_retries:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else self.settings.retry_delay_seconds
                self._wait(min(max(delay, 0.0), 60.0))
        raise AssertionError("unreachable")

    def _record_usage(self, response: Mapping[str, Any]) -> None:
        usage = response.get("usage")
        values = usage if isinstance(usage, Mapping) else {}
        self._last_usage = LLMUsage(
            request_id=str(response.get("id") or ""),
            model=str(response.get("model") or self.settings.model),
            input_tokens=_safe_count(values.get("input_tokens")),
            output_tokens=_safe_count(values.get("output_tokens")),
            total_tokens=_safe_count(values.get("total_tokens")),
        )

    @staticmethod
    def _output_text(response: Mapping[str, Any]) -> str:
        status = response.get("status")
        if status not in (None, "completed"):
            raise OpenAILLMError("OpenAI response did not complete.")
        output = response.get("output")
        if not isinstance(output, list):
            raise OpenAILLMError("OpenAI response contained no output message.")
        texts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") == "refusal":
                    raise OpenAILLMError("OpenAI refused the request.")
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        text = "".join(texts)
        if not text.strip():
            raise OpenAILLMError("OpenAI response contained no output text.")
        return text


__all__ = [
    "LLMUsage", "OpenAILLMError", "OpenAILLMProvider", "OpenAITransport",
    "OpenAITransportError", "RESPONSES_ENDPOINT", "UrllibOpenAITransport",
]

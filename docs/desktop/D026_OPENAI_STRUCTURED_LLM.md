# D026 — OpenAI structured LLM adapter

Status: **Partial** (2026-09-21). The adapter, composition and offline transport
coverage pass. A credentialed OpenAI smoke remains deliberately external to CI.
The user explicitly authorized starting D026 while its D025 release dependency is
still Partial; this does not change D025 or M9 status.

## Selected provider and boundary

D026 selects one vendor: OpenAI, using the Responses API and strict Structured
Outputs. The provider implements the existing `LLMProvider` protocol for both
editable D014 script sections and versioned D015 visual prompts. The application
services remain provider-neutral and retain their existing validation and atomic
publication rules.

The request follows the official [Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs):
`text.format.type` is `json_schema`, `strict` is true, and the existing D014/D015
schema is supplied after removing only its local `$id` annotation. Requests set
`store` to false. Responses are accepted only when completed and containing an
`output_text` message; refusals, incomplete responses, malformed JSON and non-object
JSON fail closed before application persistence.

## Configuration and credentials

Installed composition is opt-in. When `AICS_LLM_PROVIDER` is absent, the desktop
keeps real LLM generation disabled and manual/mock paths remain unchanged.

| Environment variable | Meaning |
| --- | --- |
| `AICS_LLM_PROVIDER` | Must be `openai` to enable the adapter |
| `AICS_OPENAI_MODEL` | Required explicit model identifier |
| `AICS_OPENAI_API_KEY_ENV` | Optional name of the secret variable; defaults to `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | Secret value read only when a request is made |
| `AICS_OPENAI_TIMEOUT_SECONDS` | Request timeout, 1–300 seconds |
| `AICS_OPENAI_MAX_OUTPUT_TOKENS` | Output-token ceiling, 1–100000 |
| `AICS_OPENAI_MAX_RETRIES` | Retry count for rate limits/transient failures, 0–5 |
| `AICS_OPENAI_REQUEST_INTERVAL_SECONDS` | Minimum local interval between attempts, 0–60 seconds |
| `AICS_OPENAI_RETRY_DELAY_SECONDS` | Default retry delay, 0–60 seconds |

Provider settings reject inline API keys, tokens, credentials, unknown fields and
custom endpoints. The factory stores only the environment-variable name. Transport
errors never include response bodies, authorization headers or request URLs.
Authentication, rejection, rate-limit, timeout/network and service failures are
reported through fixed credential-safe messages.

The adapter records response ID, effective model and input/output/total tokens in a
safe usage snapshot. Its generation identity contains only provider, API and model,
so D015 freshness can distinguish configuration changes without retaining secrets.

## Service wiring and failure behavior

`build_llm_provider` selects only `mock` or `openai` through `ProviderConfig` and
supports the existing registry. Installed desktop startup composes OpenAI only from
the explicit environment selection. The same instance is passed to whole-script
generation and scene prompt composition.

D014 validates the returned complete sections before saving a new script revision.
D015 validates the exact prompt object and creates an immutable unselected revision
before explicit selection. Consequently schema errors, refusals, HTTP failures and
concurrent/stale changes preserve the currently active script or prompt selection.

## Verification

Offline tests inject a transport and cover exact Responses payloads, script and
prompt editing, usage, missing credentials, strict schema handling, malformed and
refused output, rate-limit retry, throttling, timeouts/error redaction, factory
selection and preservation of active revisions.

The opt-in real smoke makes two credentialed calls and writes only safe counts and
usage metadata:

```powershell
$env:AICS_LLM_PROVIDER = "openai"
$env:AICS_OPENAI_MODEL = "<supported-model>"
$env:OPENAI_API_KEY = "<secret>"
.\.venv-ci311\Scripts\python.exe -m app.tooling.openai_llm_smoke `
  --model $env:AICS_OPENAI_MODEL `
  --output .tmp/d026-openai-smoke/report.json
```

The secret must be injected by the operator and must not be placed in the report,
project DTO, command arguments or repository. Until this smoke passes against the
chosen account/model, D026 remains Partial.

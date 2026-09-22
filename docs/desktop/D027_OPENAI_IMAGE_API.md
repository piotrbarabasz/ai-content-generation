# D027 — OpenAI image API adapter

Status: **Partial** (2026-09-22). The OpenAI Images adapter, installed composition,
offline transport tests and D017 publication/selection integration pass. A
credentialed remote smoke remains intentionally external to CI. The user explicitly
requested D027 while D025 is still Partial; this exception does not change D025/M9.

## Selected provider and API boundary

D027 selects one provider: OpenAI Images API. It uses the fixed
`POST /v1/images/generations` endpoint described by the official
[Image generation guide](https://platform.openai.com/docs/guides/image-generation).
The model is always an explicit `gpt-image-*` identifier rather than an implicit
latest alias. The request maps the pinned visual prompt, exact supported size,
quality, background, moderation and PNG/JPEG output format. JPEG compression is
sent only for JPEG requests.

The adapter accepts exactly one `data[0].b64_json` result. It rejects URL-only,
missing, oversized or invalid base64 responses. Pillow verifies and fully loads a
single-frame PNG/JPEG; the returned format and dimensions must exactly equal the
request. D017 independently decodes the bytes again under its storage limits before
conditional publication.

## Contract extension required by the real provider

The existing provider-neutral request always contains a seed, while OpenAI Images
does not expose deterministic seeding. D027 therefore defines zero as the existing
request's neutral “unspecified” seed. Providers declaring `seeded=False` accept only
zero and reject nonzero values before enqueue. Existing seeded providers retain
their behavior.

`ImageGenerationCapabilities` now optionally records exact supported dimensions
and safe effective settings. `ImageGenerationResult` now carries JSON-compatible,
credential-free result metadata. D017 persists these values with the request and
provider identity, so quality/background/model changes bypass cache and the final
artifact records its actual provenance.

## Configuration and secrets

Installed composition is opt-in:

| Environment variable | Meaning |
| --- | --- |
| `AICS_IMAGE_PROVIDER` | Must be `openai` to enable remote image generation |
| `AICS_OPENAI_IMAGE_MODEL` | Required explicit `gpt-image-*` model identifier |
| `AICS_OPENAI_API_KEY_ENV` | Secret variable name; defaults to `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | Secret read only when a request is made |
| `AICS_OPENAI_IMAGE_QUALITY` | `auto`, `low`, `medium` or `high` |
| `AICS_OPENAI_IMAGE_BACKGROUND` | `auto`, `opaque` or `transparent` |
| `AICS_OPENAI_IMAGE_MODERATION` | `auto` or `low` |
| `AICS_OPENAI_IMAGE_OUTPUT_COMPRESSION` | JPEG compression value, 0–100 |
| `AICS_OPENAI_IMAGE_TIMEOUT_SECONDS` | Timeout, 1–300 seconds |
| `AICS_OPENAI_IMAGE_MAX_BYTES` | Maximum decoded result, at most 16 MiB |
| `AICS_OPENAI_IMAGE_MAX_RETRIES` | Transient retry count, 0–5 |
| `AICS_OPENAI_IMAGE_REQUEST_INTERVAL_SECONDS` | Minimum local request interval, 0–60 seconds |
| `AICS_OPENAI_IMAGE_RETRY_DELAY_SECONDS` | Default retry delay, 0–60 seconds |

Inline credentials, custom endpoints and unknown settings are rejected. Response
bodies, URLs and authorization headers never enter errors. The provider metadata
omits the API-key environment name and transport controls; project artifacts retain
only effective generation settings and bounded response provenance.

## Publication, variants and cancellation

Desktop startup composes the provider only when explicitly enabled and passes it
through the existing scene/image service. The panel selects the first supported
size and disables seed editing for the unseeded provider. Manual import remains
available without any remote provider.

The existing D017/D040 job boundary remains authoritative. Successful generation
creates a candidate without erasing older variants; explicit scene selection can
move between them. Timeout, rate limit, corrupt content, dimension mismatch,
obsolete prompt or cancellation fails/acknowledges the attempt without replacing a
valid candidate or the user's selected image. A response that arrives after
cancellation is decoded but discarded before artifact publication.

## Credentialed smoke

Default tests use an injected offline transport. The explicit remote smoke writes a
verified 1024×1024 PNG and a secret-free report outside the repository:

```powershell
$env:AICS_IMAGE_PROVIDER = "openai"
$env:AICS_OPENAI_IMAGE_MODEL = "<supported-gpt-image-model>"
$env:OPENAI_API_KEY = "<secret>"
.\.venv-ci311\Scripts\python.exe -m app.tooling.openai_image_smoke `
  --model $env:AICS_OPENAI_IMAGE_MODEL `
  --quality low `
  --output-dir .tmp/d027-openai-smoke
```

The report records model/settings, dimensions, byte size, checksum and safe response
metadata. Until an operator runs this paid remote check with an available account
and model, D027 remains Partial.

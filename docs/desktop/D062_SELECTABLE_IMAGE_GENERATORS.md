# D062 — Selectable image generators

## Catalog and application path

The desktop composition builds a small catalog of configured generator profiles.
Visuals reads each option's stable ID, label, orientation sizes, output format and
seed capability. It does not branch on provider/model classes. Each catalog entry
uses the existing `ImageGenerationService`; entries share the project publication
index, job coordinator, image history and selection chain.

The `AICS_IMAGE_PROVIDER` value selects the default catalog choice. A configured
second provider can appear as another choice. Missing local installations do not
block a configured GPT Image 2 choice. Missing OpenAI credentials are checked only
when the user starts an OpenAI generation. No provider is automatically substituted
for another after a generation failure.

| Generator | Landscape source | Portrait source | Output | Seed |
| --- | ---: | ---: | --- | --- |
| Local — Stable Diffusion 1.5 | 640×360 | 360×640 | PNG | Enabled |
| OpenAI — GPT Image 2 | 1280×720 | 720×1280 | WebP | Disabled |

GPT Image 2 sizes are multiples of 16 and match the editor's 16:9 and 9:16
orientations. The previously proposed 640×1024 size is 5:8, not 9:16; 1024×640
is 8:5, not 16:9. D062 does not crop, stretch or reframe either result.

GPT Image 2 dimensions are checked before provider execution: each edge is divisible
by 16, area is 655,360–8,294,400 pixels, the longest edge is at most 3,840 pixels,
and the aspect ratio is at most 3:1. This validation is not limited to the two
Visuals presets.

## GPT Image 2 profile

OpenAI requests use the official Python SDK and `POST /v1/images/generations`, with
the explicit model `gpt-image-2`, `n=1`, `quality=low`, WebP output and compression
90 by default. The low quality profile limits source-scene generation cost. The
existing `OPENAI_API_KEY` is read only when Generate image is selected; it is not
part of provider identity, artifact metadata or error messages. The [official SDK
image generation method](https://github.com/openai/openai-python/blob/main/src/openai/resources/images.py)
documents the generation endpoint, `output_format`, compression and GPT Image 2
arbitrary-size support.

The SDK client is constructed lazily during generation. SDK retries are disabled so
the provider owns one bounded retry policy: up to two retries, exponential delay
from `AICS_OPENAI_IMAGE_RETRY_DELAY_SECONDS`, a 60-second maximum wait, and a numeric
server `Retry-After` value when supplied. Authentication and ordinary 4xx failures
are not retried. Remote response bodies and SDK exception text are not shown to the
user.

## WebP retention and downstream use

WebP is retained under a truthful `.webp` source name. The shared image request,
result, capability, decoder, SceneImage and import-extension contracts accept WebP
while retaining PNG/JPEG support. Decoding remains bounded and rejects animated
images. Qt can display the retained WebP bytes. Timeline/render staging uses Pillow
to convert the selected source into its existing PNG input, and Real-ESRGAN accepts
the retained WebP bytes as its source format.

## Cache and provenance

Image request fingerprints include the provider capability identity and the full
request. GPT Image 2 identity includes its provider, exact model, profile version,
quality, background, moderation and compression. The selected output format and
dimensions also enter the request fingerprint. Local SD and OpenAI therefore cannot
share generated-image cache entries, even for the same scene prompt. Retained
generation provenance carries the provider/model identity; history labels use that
identity when present and keep the filename fallback for older artifacts.

## D060 final-resolution flow

Draft / Source keeps the selected generated source. Other final-resolution presets
continue using one local Real-ESRGAN x4 inference followed by the existing exact
Lanczos target resize. GPT Image 2 source dimensions of 1280×720 and 720×1280 produce
native x4 intermediates of 5120×2880 and 2880×5120. Both fit the current 8,192-pixel
native edge and 64-million-pixel native bounds. FHD, QHD and 4K targets remain within
the existing 8,192 edge and 32-million-pixel output bounds. D062 does not make a
second paid image request for final-resolution output.

## Configuration

`.env.example` selects local SD as the default and configures GPT Image 2 as an
additional option. It reuses `OPENAI_API_KEY`; do not add another API key variable.
The developer's ignored `.env` is not changed by D062. Add these settings there to
enable the second option:

```env
AICS_OPENAI_IMAGE_MODEL=gpt-image-2
AICS_OPENAI_IMAGE_QUALITY=low
AICS_OPENAI_IMAGE_OUTPUT_COMPRESSION=90
```

The OpenAI option can appear without a key. A generation attempt then reports the
missing `OPENAI_API_KEY` variable without affecting the local generator.

## Offline tests and manual paid smoke

Automated tests use fake providers and an injected transport; they do not make paid
requests. The explicit one-image smoke is separate from pytest and requests only
GPT Image 2 at low quality, `n=1`, 1280×720, WebP. It saves the result and a report
outside project artifacts:

```powershell
python run_d062_openai_image_smoke.py --output-dir "D:/AI Content Studio/d062-smoke"
```

The report contains success/failure, model, dimensions, format, byte count, SHA-256,
duration and safe usage metadata. It does not print the API key, headers or base64.
Do not run this command without explicit approval for the paid request.

The current automated acceptance covers contract validation, SDK request mapping,
retry/error handling, catalog selection, async execution, WebP retention/display,
and D060 bounds. The non-paid GUI review and credentialed GPT Image 2 GUI smoke are
recorded separately; D062 remains Partial until the credentialed GUI smoke passes.

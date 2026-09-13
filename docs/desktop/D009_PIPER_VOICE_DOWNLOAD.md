# D009: on-demand Piper voice download

D009 adds a blocking application service for downloading the existing curated
Piper voices. D008's merged private runtime, active-install verification and
typed health contract supply its implemented dependency. Download starts only
after that runtime verifies as ready. D008's separate clean-Windows release gate
remains pending; this task neither changes its status nor certifies that gate.

## Public composition

```python
from app.runtime.profiles import HostCapabilities
from app.runtime.provisioning import PiperProvisioner
from app.runtime.voice_download import PiperVoiceDownloader

runtime = PiperProvisioner(configured_runtime_root,
    HostCapabilities("windows", "x86_64", ("cpu",)))
downloader = PiperVoiceDownloader(configured_model_root, runtime)
voice = downloader.download("pl_PL-gosia-medium", language_id="pl_PL")
# voice.model_path / voice.config_path resolve inside configured model storage.
recovered = downloader.installed("pl_PL-gosia-medium", language_id="pl_PL")
assert recovered == voice
```

Desktop composition schedules `download()` off its UI/event-loop thread. Optional
`canceled()` and `progress(filename, downloaded_bytes, total_bytes)` callbacks
support cancellation and progress without Qt, HTTP application routes or provider
instances. Callbacks execute on the calling service thread. `installed()` reads
and verifies local state without network traffic or loading any optional runtime.
Download requires the D008 runtime; discovery of already installed voice metadata
does not execute it. Download/discovery imports no Piper, ONNX, NumPy or Torch.

The caller supplies the catalog locale `pl_PL` or the existing provider/selection
language `pl`; that explicit alias resolves to the same `pl_PL` voice. Unknown
voices and mismatched language fail before source access or model-storage
writes. This is an installed-version index per voice, not a global project voice
selection. Source-language defaults, provider identities and existing catalog
payloads remain unchanged. Actual synthesis and section integration belong to
D010/later work.

## Provenance and transport

All URLs, revisions, required files and expected hashes come from
`providers/piper_catalog.py`. The downloader accepts a supported key, not a
user-supplied manifest, repository or URL. It downloads the ONNX model, required
ONNX JSON companion and MODEL_CARD, retaining the complete catalog metadata in
the receipt. All five existing curated keys are supported by the service; the
explicit real smoke exercises Gosia only.

The legacy catalog pins are **MD5** and remain unchanged for existing API/cache
compatibility. D009 explicitly verifies that algorithm, and records SHA-256 and
byte size of the resulting local files as additional installation evidence.
The recorded SHA-256 is not a newly curated publisher pin or signature. The
catalog's legacy engine-license field is preserved as provenance; D045 remains
the source for the installed Piper package's own version/license metadata.

`VoiceHTTPTransport` uses verified HTTPS and accepts redirects only to the
publisher's Hugging Face/CDN domains. Errors do not print signed redirect URLs.
An initial range probe determines each response's total size before body
downloads. Responses must have identity encoding and consistent Content-Length
and Content-Range. Files are bounded to 512 MiB for ONNX and 1 MiB each for the
config and model card. Read/connect operations have a 30-second timeout.

The downloader requests `Range: bytes=<persisted-size>-`, with a strong ETag
from the current probe as `If-Range` when available. A matching 206 response is
appended; a full 200 response restarts the file after another space check.
Unexpected offsets/totals, unusable 416 responses, encoded responses, truncated
bodies and inconsistent framing cannot activate a model. Partial progress is
measured from persisted bytes, and the complete catalog checksum is always
verified before publication. This follows the full/partial response distinction
in [HTTP Semantics, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-14).

## Storage, interruption and activation

Model storage uses a catalog fingerprint, derived from the complete curated
payload including provenance, to isolate versioned download state:

```text
<configured-model-root>/
  provision.lock
  downloads/<catalog-fingerprint>/<filename>.part
  candidate-<uuid>/...
  versions/<catalog-fingerprint>/<model, config, MODEL_CARD, voice.json>
  active/<voice-key>.json
```

The D008 OS-lock helper serializes writes to this model root and releases on
process death. Free-space checks cover the remaining downloads plus a default
64 MiB reserve and repeat before writes. Disk-space exhaustion or write failure
cannot publish an active pointer. Another explicit call resumes retained partial
files after a connection failure or cancellation. Completed partials are reused
and rehashed. Bad hashes/oversized prefixes are retained under `.bad-<uuid>` names
and excluded from publication; a later retry downloads a fresh file.

Verified files are moved into a private candidate, whose config must declare the
catalog language and sample rate. A version receipt records their hashes, sizes
and provenance. The candidate directory is renamed into its final version, then
an atomic pointer replacement makes it active. Restart recovers a complete
version left between those publication steps without another download. A retry
that is still canceled leaves that version inactive. A failure while assembling
a candidate keeps it inactive and may require downloading already completed files
again; network-transfer interruption retains resumable `.part` files.

Index reads recheck the current catalog, receipt and each complete file. Missing,
modified or extra files and invalid pointers fail closed. Relative metadata
permits relocation. Failure to download another voice does not remove the
existing voice. Basic filename/link/reparse checks protect these paths; this is
not a replacement for the broader D044 storage policy task.

## Acceptance and evidence

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Interrupted download resumes | Offline disconnect/cancel tests and real HTTP 206 continuation after 1 MiB | PASS |
| Invalid hash prevents activation | Model/config/card and corrupted-prefix tests, retained bad bytes | PASS |
| Insufficient space prevents activation | Initial and midstream exhaustion tests | PASS |
| Missing companion prevents activation | Missing config and incomplete/invalid local-version tests | PASS |
| Selected language matches installed voice | Pre-download selection check plus hash-valid config language/sample-rate rejection | PASS |

Focused validation, isolated Python 3.11.9, Windows, 2026-09-13:

```text
python -m pytest backend/tests/unit/test_voice_download.py backend/tests/unit/test_voice_http.py backend/tests/unit/test_t069.py backend/tests/unit/test_t084.py backend/tests/unit/test_runtime_provisioning.py backend/tests/integration/test_piper_health.py -W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning
```

Result: **122 passed**, 14.03 s, including 62 new D009 cases.
Default tests are offline, use synthetic models,
fake HTTP/range streams and disk-space responses, and include an isolated import
guard against optional runtimes/network access. Full-suite and diff-check results
are recorded in the D009 implementation-plan entry.

The explicit network smoke is separate from pytest/CI:

```text
python packaging/d009/smoke.py --runtime-root <verified-D008-root> --output <new-ignored-directory>
```

Observed source:
`rhasspy/piper-voices`, revision `5e74c24a88ed7d31e308633fa1542433ce2b28d4`,
voice `pl_PL-gosia-medium`, language `pl_PL`. The source is the
[existing pinned voice directory](https://huggingface.co/rhasspy/piper-voices/tree/5e74c24a88ed7d31e308633fa1542433ce2b28d4/pl/pl_PL/gosia/medium).
The model downloaded 1,048,576 bytes, cancellation left it inactive, and a new
service resumed with `206 bytes 1048576-63201293/63201294`. Config and MODEL_CARD
then completed; hashes, language and restart identity all passed.

| File | Bytes | Measured SHA-256 |
| --- | ---: | --- |
| ONNX | 63,201,294 | `38f66464240ed74f186e6b7dc13c6e3b22e023426299f25c2b3cc9dfa9373fbc` |
| ONNX JSON | 6,920 | `956cd5b2a08dca5e780ad584a6d2e971ba3bd7fcd06297dfa6cd85c9fbcd3d42` |
| MODEL_CARD | 274 | `0b9b919260bc42750d945791e940aae22dde757d64d665a0580eaadc32b5bb62` |

Catalog fingerprint:
`520a19d6b3c84a01972076f33e3ef8059067795eab7f15daed466deddc0481ec`.
The full receipt and HTTP range evidence remain ignored at
`.tmp/d009-real-smoke-v2/report.json`. A fresh D008 runtime was provisioned from
its approved offline cache under `.tmp/d009-runtime`; an older ignored runtime
correctly failed its exact worker-source revision check after the branch merge.
No D008 implementation change or integrity bypass was used.

## Review and limitations

The service preserves the provider-neutral workflow and existing catalog/API
contracts. It does not synthesize speech, change project selections, download
arbitrary repositories, install runtime updates or begin D010. Review HTTP range
handling, disk-space checks, language validation and the publication boundary.

MD5 is a legacy integrity constraint, not collision-resistant publisher
authentication; a future curated pin migration needs separate review. Partial,
bad and abandoned candidate files consume space; automatic cleanup and model
updates are outside D009. Blocking network reads bound cancellation latency to
the read timeout, not immediate interruption. Runtime and model roots are trusted
local configuration, and receipts are not a defense against a local attacker
who can rewrite both files and metadata. D008's clean-Windows gate remains a
release limitation even though D009's own acceptance passes.

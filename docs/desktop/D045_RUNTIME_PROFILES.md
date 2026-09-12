# D045: runtime profile manifest contract

D045 supplies the reproducible profile boundary required by D008. Its code
parses and checks metadata only. Installing packages, downloading voices, running
health probes, interpreting commands from a manifest and plugin discovery remain
outside this task. D006 is implemented and its 26 tests passed before changes.

## Values, schema and approval

`runtime/profiles.py` is the executable schema: frozen `ArtifactPin`, `PackagePin`,
`NativeRuntimePin`, `RuntimeProfile`, `HostCapabilities`, and typed health values.
Schema v1 intentionally supports one CPython 3.11 / Windows x64 / CPU target.
It is not a general wheel resolver. A profile records exact interpreter/package
versions, filenames, SHA-256, byte size, source and license references, explicit
dependency pins, native DLL requirements, external model needs and a fixed
health-check identifier. There are no shell strings, executable entry points,
install hooks, import paths or arbitrary environment overrides in the schema.

Validation rejects unknown/missing fields, unsupported schemas/targets, non-exact
versions, unsafe filenames/URLs, invalid hashes/sizes, incompatible wheel tags,
duplicate packages, missing/conflicting dependencies, cycles and unrelated extra
packages. JSON has a 256 KiB limit and rejects duplicate keys. Published artifacts
must use exact filenames at the approved Python/PyPI/Microsoft download hosts.
Provenance URLs require credential-free HTTPS. No machine paths are serialized.

The manifest is `backend/app/runtime/piper_cpu_windows_x64.json`, included as
package data. `profile_catalog` exposes its explicit ID and curated fingerprint;
it does not scan directories, load plugins or accept a user-selected file.
`RuntimeProfile.from_json` validates syntax/structure. Before installation D008
must also call `require_approved(profile, host)` or use `load_approved_profile`,
which checks both host compatibility and exact curated content. Changing a hash,
version, URL, dependency, model requirement or license requires updating the
reviewed manifest and its allowlist fingerprint. Schema validity alone does not
authorize new contents.

```python
from app.runtime.profile_catalog import load_approved_profile
from app.runtime.profiles import HostCapabilities

# Composition supplies normalized host metadata; discovery does not probe hardware.
profile = load_approved_profile(
    "piper-cpu-windows-x64", HostCapabilities("windows", "x86_64", ("cpu",)))
print(profile.fingerprint)
```

Fingerprint:
`4b4ac9e5c0f6f8d650e66b41ec69580e45736cebead2ea42e8e421eb00fbede0`.
It hashes canonical sorted JSON; package/dependency ordering is normalized.
Returned values do not retain mutable caller-owned dictionaries or lists.

## Curated Piper profile 1.0.0

| Component | Exact version | Selected artifact |
| --- | --- | --- |
| CPython | 3.11.9 | `python-3.11.9-embed-amd64.zip` |
| Piper | 1.6.0 | `piper_tts-1.6.0-cp39-abi3-win_amd64.whl` |
| ONNX Runtime | 1.28.0 | `onnxruntime-1.28.0-cp311-cp311-win_amd64.whl` |
| pathvalidate | 3.3.1 | `pathvalidate-3.3.1-py3-none-any.whl` |
| NumPy | 2.4.6 | `numpy-2.4.6-cp311-cp311-win_amd64.whl` |
| flatbuffers | 25.12.19 | `flatbuffers-25.12.19-py2.py3-none-any.whl` |
| protobuf | 7.35.1 | `protobuf-7.35.1-cp310-abi3-win_amd64.whl` |
| packaging | 26.3 | `packaging-26.3-py3-none-any.whl` |
| Microsoft VC++ x64 | 14.44.35211.0 | `VC_redist.x64.exe` |

The Python/Piper versions retain the existing development ABI and Piper version;
there is no implicit upgrade to PyPI latest. CPython's source is its
[official 3.11.9 release](https://www.python.org/downloads/release/python-3119/).
Piper's [1.6.0 release metadata](https://pypi.org/project/piper-tts/1.6.0/) supplies
the Windows ABI3 wheel, Python requirement, hash, provenance and GPL-3.0-or-later
license. Each transitive wheel's exact PyPI JSON/release URL is retained in the
manifest. No training, HTTP, alignment, Chinese-language or development extras
are selected. Piper depends on ONNX Runtime/pathvalidate; ONNX Runtime depends on
flatbuffers/NumPy/packaging/protobuf; these selected leaves have no active runtime
dependencies for this target.

During implementation all seven wheel downloads were hashed against PyPI and
their actual `.dist-info/METADATA` requirements checked for Windows / CPython
3.11.9 with no extras. Every active requirement resolves to an explicit pin and
satisfies the declared version constraint. These one-time provenance checks used
public artifacts under ignored `.tmp/d045/`; application discovery and tests have
no network behavior and do not need these downloads.

Static PE inspection found that ONNX Runtime's DLL/PYD imports `MSVCP140.dll`,
`MSVCP140_1.dll`, `VCRUNTIME140.dll` and `VCRUNTIME140_1.dll`; its linker version is
14.44. The embedded Python archive contains only the two VCRUNTIME DLLs from
this set. The manifest therefore includes a separate native-runtime pin rather
than silently assuming a developer machine's MSVC installation. The downloaded
Microsoft binary's file/product version is 14.44.35211.0; its resolved immutable
CDN URL and measured SHA-256 are recorded. The moving download shortcut is not
used in the manifest. Sources are [Microsoft's redistributable download page](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
and its [runtime license reference](https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/).
No redistributable executable or downloaded Python/Piper code was run.

The descriptor records an artifact and native requirements, not an installation
method. D008 must provide the appropriate controlled native-runtime provisioning
and fail health checks when required DLLs are unavailable or incompatible. It
must not silently install software system-wide based on a manifest command.

## Model and health boundary

The fixed model requirement identifies `curated-piper-voices`, exact ONNX/config
roles, `bundled: false` and `required_for: synthesis`. The existing Piper catalog
remains authoritative for model revisions, hashes and model licenses; this
manifest does not duplicate/download its assets. A runtime can be healthy before
a voice is downloaded; synthesis readiness still requires D009 model validation.

`piper-cpu-imports-v1` identifies trusted D008 probe logic, not code to import from
the manifest. It must report four observations: exact private interpreter,
installed package identities, native/CPU backend importability, and worker
protocol v1 handshake. It must not load a voice or synthesize audio just to
discover the profile. `ProfileHealth` supports `ready`, `not_installed`,
`incompatible` and `failed`, a timezone-aware timestamp and typed observations.
`ready` requires all four checks to pass; failure/incompatibility needs an explicit
failed observation. The report is bound to both profile ID and content fingerprint
and serializes through its own versioned payload. These values validate reported
evidence; they do not themselves run checks or prove installation.

## Acceptance and evidence

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Invalid/incompatible profiles rejected before installation | Strict schema, host/wheel compatibility, closure, provenance and content-approval tests | PASS |
| Discovery imports no Torch and loads no model | Isolated process blocks Piper/ONNX/NumPy/Torch/Qt imports, sockets and subprocess creation during discovery | PASS |
| All required package identities explicit | Seven verified target wheels, interpreter archive and MSVC artifact with versions/hashes/source/license; complete active dependency closure | PASS |
| Health outcome is typed and revision-bound | Round trips, missing/contradictory observations and mismatched profile evidence tests | PASS |

Focused command:

```text
python -m pytest backend/tests/unit/test_runtime_profiles.py backend/tests/integration/test_durable_jobs.py backend/tests/unit/test_worker_protocol.py backend/tests/integration/test_worker_lifecycle.py
```

Focused result: **125 passed**, including 48 D045 cases, 13.41 s.

Full validation: `python -m pytest backend/tests` — **771 passed**, 35.31 s.
`git diff --check` and new-file whitespace validation — PASS.
Environment: Windows, isolated Python 3.11, 2026-09-12. Tests are offline.

An offline application wheel build with `--no-deps --no-build-isolation --no-index`
confirmed that the JSON ships as package data. An isolated Python process loaded
and verified the profile directly from that wheel, outside checkout imports.
This was a resource-packaging check with existing local build tools, not an
installation or release-build qualification. Wheel and provenance scratch files
remain ignored under `.tmp/d045/`.

## Self-review and limits

The implementation is metadata-only and additive; no D006/D007 behavior, provider
contracts, existing venv scripts or project schemas change. D008 remains Planned.
The approved allowlist prevents a structurally valid replacement manifest from
selecting unreviewed artifacts. It is an application policy, not a package signing
infrastructure. No runtime, voice or system package was installed during D045.

Pinned CPython 3.11.9 is the older existing ABI baseline, not a current security
release claim. Pin updates require review and later runtime/update work. Native
DLL importability, relocation, clean-Windows provisioning and worker execution
with this complete private profile still require D008 acceptance. License fields
preserve publisher references; distribution review remains release work. Review
the pins, native dependency and fixed health contract before merge. Do not begin
D008 automatically after this task.

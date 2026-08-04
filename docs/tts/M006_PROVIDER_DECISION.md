# M006 Provider Decision: Multi-Provider Polish TTS

## Decision

M006 adds two provider integrations beside the existing Chatterbox Multilingual V3 baseline:

1. **Piper** as the fast local provider and production candidate.
2. **XTTS-v2** as an explicitly evaluation-only voice-cloning provider.

The workflow engine and `VoiceoverModule` remain provider-neutral. Selection continues through the existing `ProviderConfig`, `TTSSettings`, TTS factory and provider registry. M006 must not create a second registry or add `if provider == ...` branches to workflow orchestration.

## Why this milestone follows M005

M004 and M005 established valid WAV artifacts, optional Chatterbox integration, resumable chunk synthesis, effective synthesis identity, cache integrity and benchmark evidence. The next useful question is no longer whether local narration can be generated, but which provider and voice profile should be used for a given workflow.

Before adding more providers, M006 first formalizes two real-runtime findings
from the manual Chatterbox smoke:

- the pinned package must expose the Multilingual V3 `t3_model` API used by the adapter;
- the already-applied PCM16 output hotfix must be covered by a regression test and documented as part of the runtime contract.

These are runtime compatibility and reproducibility fixes, not changes to the provider-neutral architecture.

## Provider roles

| Provider | Role | Polish | Voice mode | Speed control | Runtime policy |
|---|---|---:|---|---|---|
| Chatterbox Multilingual V3 | quality baseline | yes | built-in or approved reference | indirect generation controls; optional post-processing later | production candidate |
| Piper | fast preview and deterministic local narration | yes | curated installed voice model | native `length_scale` | production candidate subject to distribution and voice-license review |
| XTTS-v2 | voice-cloning comparison | yes | approved reference audio required | provider controls where supported | evaluation only unless separately licensed |

## Licensing and usage policy

The implementation must distinguish technical support from permitted product use.

- Piper engine distribution is governed by its engine license, while individual voice models may have their own model-card licenses. The selected voice catalog must record the exact source, revision, checksum and license identifier for every model.
- XTTS-v2 model weights are licensed for non-commercial use under the Coqui Public Model License. The provider must therefore advertise `usage_policy: evaluation_only`, require an explicit evaluation mode and be rejected by production configuration validation.
- Reference-audio use requires a documented right or consent to clone the voice. Tests use generated fixtures and never commit private recordings.
- This document is an engineering policy record, not legal advice. A human license review remains required before distribution or paid use.

## Runtime profiles

Heavy runtimes remain isolated:

```text
.venv-ci311             # default tests, hooks and agent tooling
.venv-tts311            # Chatterbox Multilingual V3
.venv-piper311          # Piper and ONNX runtime
.venv-xtts311           # XTTS-v2 evaluation
```

Setup and health-check scripts must invoke explicit interpreter paths and must never repoint `agent.python` away from the CI environment.

## Provider capability contract

Each provider should expose deterministic JSON-compatible capability metadata without importing its heavy runtime:

```json
{
  "provider": "piper",
  "languages": ["pl"],
  "voice_modes": ["catalog"],
  "requires_reference_audio": false,
  "supports_speaking_rate": true,
  "usage_policy": "production_candidate"
}
```

Capability metadata is descriptive. Effective synthesis identity remains request-specific and includes the resolved model or voice asset, revision/checksum, language, device and effective generation settings.

## Manual comparison contract

The comparison runner must:

- use exactly the same normalized Polish text for each selected profile;
- run providers sequentially so 6 GB VRAM is not shared by multiple loaded models;
- preserve original provider output before any loudness or tempo normalization;
- write one WAV and one JSON report per profile;
- write a summary JSON and playlist;
- report provider/model/voice identity, generation time, audio duration, real-time factor, sample rate, checksum and failure reason;
- never run a real model in pytest or CI.

The first comparison set is:

```text
chatterbox_v3 / builtin / neutral
piper / pl_PL-bass-high
piper / pl_PL-darkman-medium
piper / pl_PL-gosia-medium
piper / pl_PL-mc_speech-medium
piper / pl_PL-mls_6892-low
xtts_v2_eval / approved reference audio
```

XTTS is skipped with an actionable reason when no approved reference WAV is supplied.

## Out of scope

- model training or fine-tuning;
- automatic voice scraping;
- cloning a person without documented permission;
- a graphical provider picker;
- cloud TTS integrations;
- video rendering or captions;
- model execution in CI;
- automatic acceptance of new model or voice licenses.

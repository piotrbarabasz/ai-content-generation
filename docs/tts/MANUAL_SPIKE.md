# Manual XTTS-v2 Environment Spike

This document preserves historical evidence from the former XTTS-v2 version of Epic E008. It is no longer a prerequisite for the current T051 task.

> Historical result: XTTS-v2 was technically executable, but it was not selected because manual Polish voice-quality validation failed.

## Purpose

Confirm that XTTS-v2 can generate a short Polish WAV on the target machine before the agent changes production dependency declarations. This spike is environment evidence, not a CI test and not an implementation task.

## Safety and repository rules

- Use only a speaker recording that you own or have explicit permission to use.
- Store speaker references under an ignored local directory such as `.runtime/voices/`.
- Do not commit voice recordings, embeddings, model weights, generated WAV files or credentials.
- Do not ask the autopilot to guess CUDA, PyTorch, torchaudio or Coqui package versions.

## Minimum experiment

1. Create a Python 3.11 virtual environment.
2. Install the correct PyTorch build for the local GPU or CPU.
3. Install a maintained Coqui TTS package that exposes XTTS-v2.
4. Generate 2-3 Polish sentences using:
   - model: `tts_models/multilingual/multi-dataset/xtts_v2`
   - language: `pl`
   - one approved `speaker_wav`
5. Confirm that the output is a playable 24 kHz WAV.
6. Record the exact environment below.

## Evidence template

```yaml
completed: false
operating_system: ""
python: "3.11.x"
torch: ""
torchaudio: ""
tts_package: ""
tts_package_version: ""
cuda_runtime: ""
device: "cpu-or-cuda"
gpu: ""
model: "tts_models/multilingual/multi-dataset/xtts_v2"
language: "pl"
speaker_reference_seconds: 0
generation_seconds: 0
audio_seconds: 0
sample_rate: 0
peak_vram_mb: null
result: "PASS-or-FAIL"
notes: ""
```

In the former XTTS-oriented plan, T051 treated `completed: false` or missing confirmed versions as a blocker. This rule is retained only as historical context and does not apply to the current Chatterbox-oriented T051.

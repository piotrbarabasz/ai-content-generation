# Manual XTTS-v2 Environment Spike

This document records the human-run environment experiment required before task T051 pins or documents any optional XTTS dependency versions.

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

Task T051 must treat `completed: false` or missing confirmed versions as a blocker. It must not invent dependency pins.

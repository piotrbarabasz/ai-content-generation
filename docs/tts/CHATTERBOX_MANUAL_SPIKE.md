# Manual Chatterbox Multilingual V3 Spike

This document records a completed, human-run environment and listening test for Chatterbox Multilingual V3. It is planning evidence, not a CI test or an implementation of the provider.

## Validation boundaries

### Automatic technical validation

The manual environment confirmed that the model could be loaded on the listed CUDA system and generated a technically valid Polish WAV at 24 kHz. Normal CI must remain offline: it must not import Chatterbox, torch or CUDA; load model weights; access Hugging Face; or make network calls.

### Manual quality validation

The generated Polish narration was listened to and accepted for this project. This quality result selected Chatterbox Multilingual V3 as the planned local provider. The selected voice must still be reviewed for the intended content before production use.

## Recorded evidence

```yaml
completed: true
manual_quality_confirmed: true
result: PASS
operating_system: "Windows 10 / 11, build 22631"
python: "3.11.9"
torch: "2.6.0+cu124"
torchaudio: "2.6.0+cu124"
cuda_runtime: "12.4"
device: "cuda"
gpu: "NVIDIA GeForce GTX 1660 SUPER"
gpu_vram_gb: 6
tts_package: "chatterbox-tts"
tts_package_version: "0.1.7"
source_commit: "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
model: "ChatterboxMultilingualTTS"
model_variant: "v3"
language: "pl"
sample_rate: 24000
builtin_voice: true
speaker_reference_required: false
setuptools: "80.10.2"
setuptools_constraint: "<81"
perth_package: "resemble-perth"
perth_package_version: "1.0.1"
manual_quality_result: "PASS"
```

The tested model class was `chatterbox.mtl_tts.ChatterboxMultilingualTTS`, loaded with `ChatterboxMultilingualTTS.from_pretrained(device=torch.device("cuda"), t3_model="v3")` and generated in Polish using `language_id="pl"`. The built-in voice worked without a private speaker file.

## Runtime hygiene

- Generated WAV files stay under `.runtime` and are not committed.
- Model weights stay in the external Hugging Face cache and are not committed.
- `resemble-perth` 1.0.1 imports `pkg_resources`; the optional environment therefore pins `setuptools<81` (the working spike used 80.10.2).
- CUDA dependencies remain optional and must not become requirements of the normal project test environment.
- Optional speaker-reference cloning may be supported later, but private reference paths must never be committed.

No generation time, audio duration, checksum or measured VRAM usage was recorded for this spike.

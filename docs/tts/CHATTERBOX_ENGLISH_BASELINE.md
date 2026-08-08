# Chatterbox V3 English production baseline

Chatterbox Multilingual V3 is the current high-quality source-narration
baseline. The production adapter already advertises and validates English as
`language_id=en`; provider selection still goes through `ProviderConfig`,
`TTSSettings`, `TTSFactory`, and `ProviderRegistry`.

The reproducible one-minute input is
`backend/tests/fixtures/narrations/story_en_01_1min.txt`. Its word count and
SHA-256 checksum are recorded in the adjacent `metadata.json`.

## Manual smoke command

Run this from the repository root after setting up `.venv-tts311`:

```powershell
$env:PYTHONPATH = (Resolve-Path backend)
& .\.venv-tts311\Scripts\python.exe -m app.tooling.tts_smoke --provider chatterbox_v3 --input-text-file backend/tests/fixtures/narrations/story_en_01_1min.txt --output .runtime/tts-smoke/chatterbox-en.wav --report .runtime/tts-smoke/chatterbox-en.json --language en --device cuda --overwrite
```

Use `--device cpu` only when that isolated runtime is intentionally configured
for CPU inference. Add `--audio-prompt <approved-relative-or-runtime-path.wav>`
to exercise approved reference-voice mode; without it the voice mode is
`builtin`.

The runner writes a PCM WAV and JSON evidence containing the provider,
effective synthesis identity, model variant, `language_id=en`, device, voice
mode, generation wall time, duration, real-time factor, sample rate, channels,
and SHA-256 checksum. It validates the WAV before finalizing the report.

The command is human-operated. Default tests inject fake runtimes and never
download a model, use a GPU, or make a network request. Keep generated audio,
reference recordings, model weights, and caches under ignored runtime storage.

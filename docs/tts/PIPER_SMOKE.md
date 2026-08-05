# Piper smoke and comparison

Piper is a local, human-operated runtime. Keep generated WAV files, reports and comparison outputs under ignored runtime paths.

## Manual smoke

Use the smoke runner to synthesize one WAV with a curated Piper voice:

```powershell
$env:PYTHONPATH = "backend"
.venv-ci311\Scripts\python.exe -m app.tooling.tts_smoke `
  --provider piper `
  --text "Witaj świecie." `
  --output .runtime\tts-smoke\piper.wav `
  --model-key pl_PL-gosia-medium `
  --length-scale 1.0 `
  --volume 0.5 `
  --noise-scale 0.667 `
  --noise-w-scale 0.8
```

The smoke report records the resolved effective synthesis identity, including the selected voice model and any Piper controls used for the run.

## Manual comparison

Run the comparison harness when you want same-text evidence across Chatterbox neutral and the curated Piper voices:

```powershell
scripts\run-tts-provider-comparison.ps1 `
  -Text "Witaj świecie." `
  -OutputDir .runtime\tts-comparison\demo
```

The comparison writes one WAV and one report per profile, plus a `summary.json` and `playlist.m3u8`. Profiles are processed sequentially, so one failure does not prevent later profiles from running.

## Control fields

Piper supports the following validated controls in this workflow:

- `length_scale`
- `volume`
- `noise_scale`
- `noise_w_scale`

These controls belong to Piper configuration only. They are rejected for other providers.

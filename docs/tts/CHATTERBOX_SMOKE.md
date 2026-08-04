# Chatterbox V3 manual smoke runner

`python -m app.tooling.tts_smoke` generates one local WAV and a JSON report.
It is a human-operated smoke tool: normal tests use the deterministic `mock`
provider and never download a model or call a network service.

The default provider is `mock`. To exercise the optional local Chatterbox
runtime, install it as described in [the setup guide](CHATTERBOX_SETUP.md),
then explicitly select `chatterbox_v3`. Only the V3 model is accepted.

```powershell
python -m app.tooling.tts_smoke --provider chatterbox_v3 --input-text-file .runtime/tts-smoke/fixture.txt --output .runtime/tts-smoke/chatterbox.wav --language pl --device cuda
```

The tool creates parent directories for the WAV and report. The report defaults
to the WAV path with a `.json` extension and records provider, V3 model,
device, language, word count, generation time, WAV duration and sample rate,
SHA-256 checksum, and selected voice (`builtin` unless `--audio-prompt` is
provided). It validates the final WAV before writing the report and requires a
mono, 16-bit PCM payload at 24 kHz.

Supply narration with either `--text` or `--input-text-file`; the latter reads
a UTF-8 fixture or text file. Missing, unreadable, or blank input files fail
with a nonzero exit status.

Existing WAV or report paths are protected. Pass `--overwrite` deliberately to
replace either file. Optional generation controls are `--exaggeration`,
`--cfg-weight`, `--temperature`, `--repetition-penalty`, `--min-p`, and
`--top-p`. `--help` is always available without the optional runtime.

The smoke report also captures the validated PCM parameters: channels, sample
width, sample rate, compression type, frame count and duration. This keeps the
recorded Chatterbox contract reproducible without importing the heavy runtime
from the default test environment.

Keep outputs, private speaker references, weights, and caches outside tracked
paths (for example, under `.runtime/`).

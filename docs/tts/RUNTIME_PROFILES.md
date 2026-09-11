# TTS Runtime Profiles

The local TTS stack is split into isolated virtual environments so the heavy
runtime dependencies stay out of the default application and test environment.

## Profile map

| Profile | Environment | Purpose | Explicit interpreter |
|---|---|---|---|
| Application/tests | `.venv-ci311` | Lightweight backend and offline tests | `.venv-ci311\Scripts\python.exe` |
| Chatterbox V3 | `.venv-tts311` | Optional local Chatterbox runtime and demo smoke | `.venv-tts311\Scripts\python.exe` |
| Piper | `.venv-piper311` | Local Piper preview and comparison runtime | `.venv-piper311\Scripts\python.exe` |
| XTTS-v2 | `.venv-xtts311` | Evaluation-only XTTS comparison runtime | `.venv-xtts311\Scripts\python.exe` |

## Rules

- Use the explicit interpreter for the selected profile.
- Do not activate a profile implicitly from a setup or runtime script.
- Keep the base application environment separate from optional TTS dependencies.
- Keep generated WAVs, reports, caches and comparison outputs under ignored
  runtime directories such as `.runtime/`.
- Keep voice references and model caches outside tracked paths.
- Fail fast before installation or smoke execution when the active Python
  version or runtime prerequisites are unsupported.

## Scripts

- `scripts/setup-tts-runtime.ps1` prepares the isolated TTS environments and
  validates the Chatterbox V3 runtime contract.
- `scripts/check-tts-runtime.ps1` emits a human-readable summary and a JSON
  health report for the configured profiles.
- `scripts/run-tts-demo.ps1` runs the optional Chatterbox demo through
  `.venv-tts311`.

## Expected Chatterbox checks

The Chatterbox profile validation records:

- Python 3.11.x
- matching `torch` and `torchaudio` versions
- CUDA visibility
- provider importability through `backend/app`
- one opt-in real smoke run when requested

The `check-tts-runtime.ps1` report is intended to be both readable in a shell
and consumable by automation, so it prints a short summary and a JSON payload.

# Development environment configuration (D061)

## First-time setup

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your OpenAI API key and explicitly selected text/image providers.
OpenAI narration is the primary audio path and needs no local TTS installation.
Install optional local image runtimes separately. `.env` contains secrets and
must never be committed. `AICS_ENV_FILE` can select an exact alternate file; a
missing explicit file is an error. Without it, the application looks for the
checkout-root `.env` based on its module location, regardless of the working directory.
A missing default file is allowed.

## Normal launch

```powershell
python run_app.py
```

No repeated `$env:...` commands or manual `PYTHONPATH` setting are necessary.
Paid generation requires an explicit operation budget. The launcher does not
initialize Chatterbox or verify local voice models. The former
`run_test_chatterbox.py` entry point forwards to this normal launcher.
See [D078 narration setup](D078_OPENAI_NARRATION.md) for migration and cost controls.

## Temporary override

```powershell
$env:AICS_OPENAI_MODEL = "another-model"
python run_app.py
```

The process environment wins over `.env` for that launch. Clear the PowerShell
override to return to the local file value.

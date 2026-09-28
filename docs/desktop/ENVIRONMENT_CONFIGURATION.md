# Development environment configuration (D061)

## First-time setup

```powershell
Copy-Item .env.example .env
```

Edit `.env` with installed managed runtime paths, an explicit supported OpenAI model,
and your own API key. Install the runtimes separately. `.env` contains secrets and
must never be committed. `AICS_ENV_FILE` can select an exact alternate file; a
missing explicit file is an error. Without it, the application looks for the
checkout-root `.env` based on its module location, regardless of the working directory.
A missing default file is allowed.

## Normal launch

```powershell
python run_test_chatterbox.py
```

No repeated `$env:...` commands or manual `PYTHONPATH` setting are necessary.
Provider selection remains opt-in. The launcher requires existing Chatterbox runtime
and model directories and does not download them.

## Temporary override

```powershell
$env:AICS_OPENAI_MODEL = "another-model"
python run_test_chatterbox.py
```

The process environment wins over `.env` for that launch. Clear the PowerShell
override to return to the local file value.

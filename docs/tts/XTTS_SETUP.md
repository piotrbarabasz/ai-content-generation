# XTTS-v2 evaluation setup

XTTS-v2 is a local, human-operated evaluation runtime. It is not part of the
default application install, default tests, or the production provider set.
The provider name used by the codebase is `xtts_v2_eval`.

## Runtime boundary

- Use the isolated `.venv-xtts311` environment explicitly.
- Keep the XTTS runtime, downloaded weights, approved reference WAVs, and
  generated outputs outside tracked paths.
- Do not install the optional runtime into the default CI interpreter.
- Do not run real XTTS synthesis during pytest.

## Recommended manual flow

1. Ensure `.venv-xtts311\Scripts\python.exe` exists and is Python 3.11.
2. Install the optional XTTS runtime into that environment explicitly:

   ```powershell
   .venv-xtts311\Scripts\python.exe -m pip install ".[xtts]"
   ```

3. Prepare one approved reference WAV and record a human-reviewed label:

   - `reference_audio_path`: a local WAV file you are allowed to use
   - `approved_label`: a short human label such as `consent-2026-08`

4. Run a short local synthesis check from the repository root:

   ```powershell
   .venv-xtts311\Scripts\python.exe -c "from pathlib import Path; from app.domain.enums import ProviderType; from app.domain.provider_config import ProviderConfig; from app.providers.tts_factory import build_tts_provider; config = ProviderConfig.create(workflow_config_id='xtts', provider_type=ProviderType.TTS, provider_name='xtts_v2_eval', settings={'reference_audio_path': Path('.runtime/xtts/reference.wav'), 'approved_label': 'consent-2026-08', 'usage_policy': 'evaluation_only'}); provider = build_tts_provider(config); print(provider.effective_synthesis_identity())"
   ```

## Policy boundary

- `usage_policy=production` must be rejected before any model load occurs.
- Only approved reference audio may be used.
- The provider persists only non-reversible reference evidence such as a
  checksum and approved label.
- The output must be a mono 16-bit PCM WAV with a truthful sample rate.

## Notes

The provider remains isolated behind the existing TTS factory and registry.
The setup guide is intentionally separate from the smoke and comparison tools;
those are added only when the evaluation workflow needs them.

# Piper Polish setup

Piper is a local, human-operated runtime. Do not activate virtual environments
implicitly, do not modify `git config`, and do not change `agent.python` for
this workflow.

## Runtime boundary

- Use the isolated `.venv-piper311` environment explicitly.
- Run setup and health checks from the repository root or by using explicit
  paths.
- Keep downloaded model files, caches, and private reference paths outside the
  tracked repository tree.
- Do not download models during pytest.

## License split

The engine license and the voice-model licenses are separate review steps.

- Engine license: `MIT`
- Voice model licenses: recorded per catalog entry in
  [PIPER_VOICES.md](PIPER_VOICES.md)

## Recommended manual flow

1. Ensure `.venv-piper311\Scripts\python.exe` exists and is Python 3.11.
2. Install the optional Piper runtime into that environment explicitly:

   ```powershell
   .venv-piper311\Scripts\python.exe -m pip install ".[piper]"
   ```

3. Run the setup helper for one curated voice key:

   ```powershell
   scripts\setup-piper-runtime.ps1 -VoiceKey pl_PL-gosia-medium
   ```

4. Run the health check after setup:

   ```powershell
   scripts\check-piper-runtime.ps1
   ```

## What the setup helper does

The setup helper uses catalog metadata from
`backend/app/providers/piper_catalog.py` as the source of truth. It resolves
only the curated Polish voices, downloads the catalog-defined files into a
staging directory, verifies each downloaded checksum, and only then promotes
the verified files into the final runtime location.

The helper reports:

- selected voice key and voice name;
- source repository and immutable revision;
- expected sample rate;
- per-file checksum verification status;
- model and engine license identifiers;
- final activation path for the verified runtime assets.

If a checksum fails, treat the download as untrusted and restart from the
staging step.

## Runtime location

Keep the prepared assets under a repository-local runtime directory such as
`.runtime\piper\voices\<voice-key>`. Do not embed absolute or user-specific
paths in configuration or tracked files.

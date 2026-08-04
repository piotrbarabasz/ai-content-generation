# Optional Chatterbox Multilingual V3 setup

Chatterbox is a local, human-operated runtime.  It is not needed to run the
application, collect the normal test suite, or use the deterministic mock TTS
provider.  The project requires Python 3.11.

## Validated runtime matrix

The compatibility contract documented here was reproduced against the
following locked runtime values:

| Component | Validated value |
|---|---|
| Python | `3.11.9` |
| torch | `2.6.0+cu124` |
| torchaudio | `2.6.0+cu124` |
| CUDA runtime | `12.4` |
| Chatterbox package | `chatterbox-tts==0.1.7` |
| Chatterbox model class | `ChatterboxMultilingualTTS` |
| Chatterbox selector | `t3_model="v3"` |
| Chatterbox source revision | `5de7a54aa4e5e2baadb0182dde554908b48b85c2` |
| setuptools | `80.10.2` with the repository pin `setuptools<81` |

## Install an isolated optional environment

Create a Python 3.11 virtual environment, then install PyTorch and torchaudio
appropriate for the intended CPU or CUDA device from the official PyTorch
selector.  Do not add either package to `backend/requirements.txt` or the
default project dependencies.

After installing the device-specific pair, install the pinned optional project
dependencies:

```powershell
python -m pip install ".[chatterbox-v3]"
```

The extra pins are `chatterbox-tts==0.1.7`, `resemble-perth==1.0.1`, and
`setuptools<81`.  Do not substitute an unpinned source or version without
recording a new spike and updating this matrix.

## Runtime boundaries

The adapter is intentionally lazy: normal imports and tests must not import
Chatterbox, PyTorch, CUDA, download weights, or access a network.  Run real
generation only through the manual smoke workflow once it is available.

Use the model's built-in voice by default.  Speaker-reference cloning is a
future optional capability; never require it and never put a private reference
file, generated WAV, model weights, or cache under a tracked path.  Local
runtime outputs belong in `.runtime/`, while Hugging Face caches and common
local reference directories are ignored by this repository.

See [the recorded manual spike](CHATTERBOX_MANUAL_SPIKE.md) for the successful
Windows/CUDA configuration and Polish V3 quality result.

## Exact validated package pins

The manually validated Windows CUDA runtime used:

- `torch==2.6.0+cu124`
- `torchaudio==2.6.0+cu124`

These packages must remain a matching pair and are installed only in the
isolated Chatterbox runtime environment.

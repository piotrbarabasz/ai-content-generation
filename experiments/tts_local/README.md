# Local Polish TTS model laboratory

This directory is a small, isolated laboratory for manual listening tests of individual Polish text-to-speech models. It is not a production integration, benchmark harness, provider layer, or quality claim. Nothing here is connected to the backend application.

Every runner is standalone and uses a separate virtual environment. Model dependencies conflict, so do not combine the environments. Commands below are PowerShell-compatible and invoke each environment's Python executable directly; no environment activation is required.

## Layout

```text
experiments/tts_local/
├── README.md
├── benchmark_pl.txt
├── run_supertonic3.py
├── run_moss_tts_nano.py
├── run_omnivoice.py
├── run_chatterbox_v3.py
├── run_piper.py
├── run_voxcpm2.py
└── run_moss_tts_realtime.py
```

Local environments, upstream checkouts, references, model caches, and outputs belong under `.runtime/tts-experiments/`. The existing Piper cache stays under `.runtime/piper/`. These locations are ignored by Git.

## Shared comparison rules

All runners read `benchmark_pl.txt` as UTF-8 and pass the same complete text to the model. The runner does not rewrite, summarize, or add prompt hints. A model's own mandatory text processing may still occur; known unavoidable processing is recorded in the JSON report. Native sample rate and channel count are preserved. There is no resampling, denoising, loudness normalization, or speed post-processing.

Piper is the sole exception for added silence: `--sentence-silence` inserts zero PCM only between sentence chunks returned by Piper's own phonemizer. The complete original text, including punctuation, is passed to Piper at once; commas do not receive artificial pauses.

Use the same `--reference-audio` and exact `--reference-text-file` for every cloning-capable model. Reference audio may be used only when you own the recording or have explicit permission to use the speaker's voice. Never commit reference recordings or private transcripts.

## Model overview

The GTX assessment is a conservative setup guide, not a measured quality or memory benchmark.

| Script | Official model ID | Intended device | Reference | GTX 1660 SUPER 6 GB | Code license | Model-weight license | Status |
|---|---|---|---|---|---|---|---|
| `run_supertonic3.py` | `Supertone/supertonic-3` | CPU | No; built-in `M1` | Easy CPU path | MIT | OpenRAIL-M | lightweight |
| `run_moss_tts_nano.py` | `OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX` + `OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX` | CPU | Optional; built-in voice otherwise | Easy CPU path | Apache-2.0 | Apache-2.0 | lightweight |
| `run_omnivoice.py` | `k2-fsa/OmniVoice` | CUDA preferred, CPU possible | Audio and exact transcript required by this lab | Tight/uncertain; use deliberately | Apache-2.0 | CC-BY-NC (version not stated on current card) | baseline |
| `run_chatterbox_v3.py` | `ResembleAI/chatterbox`, `t3_mtl23ls_v3` | CUDA preferred | Optional | Potentially tight; close other GPU workloads | MIT | MIT | baseline |
| `run_piper.py` | `rhasspy/piper-voices:pl_PL-darkman-medium` | CPU | No | Easy CPU path | GPL-3.0 | Piper voice repository: MIT; darkman dataset card: CC0 | baseline |
| `run_voxcpm2.py` | `openbmb/VoxCPM2` | High-memory CUDA or slow CPU | Optional | Do not run automatically; no 6 GB fit claim | Apache-2.0 | Apache-2.0 | experimental-heavy |
| `run_moss_tts_realtime.py` | `OpenMOSS-Team/MOSS-TTS-Realtime` + `OpenMOSS-Team/MOSS-Audio-Tokenizer` | High-memory CUDA | Optional | Do not run automatically; upstream measurements use L20 | Apache-2.0 | Apache-2.0 | experimental-heavy |

Licenses for code and weights are listed separately. “Commercial usage appears permitted” below only reports the current upstream labeling; it is not a legal conclusion. Review the upstream license before production use, and check every model license again before any production integration.

## Supertonic 3 setup

- Official package: `supertonic`; no checkout required.
- Recommended here: Python 3.11 (upstream supports Python 3.9+).
- Expected output: 44.1 kHz WAV.
- CPU is the supported runner path. The current SDK call used here does not expose a device selector.
- Code is MIT; weights are OpenRAIL-M. Commercial permission depends on the model license terms; review upstream.
- Sources: [Python SDK](https://github.com/supertone-inc/supertonic-py), [main repository](https://github.com/supertone-inc/supertonic), [model card](https://huggingface.co/Supertone/supertonic-3).

```powershell
py -3.11 -m venv .\.runtime\tts-experiments\venvs\supertonic3
.\.runtime\tts-experiments\venvs\supertonic3\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\supertonic3\Scripts\python.exe -m pip install supertonic
```

The official SDK currently stores its approximately 400 MB first-run download in its own `~/.cache/supertonic3` default rather than the laboratory cache directory.

Run:

```powershell
.\.runtime\tts-experiments\venvs\supertonic3\Scripts\python.exe .\experiments\tts_local\run_supertonic3.py
```

## MOSS-TTS-Nano setup

- Official runtime: editable checkout of `OpenMOSS/MOSS-TTS-Nano`; the runner calls its packaged `moss_tts_nano.cli` with `--backend onnx`.
- Recommended upstream environment: Python 3.12.
- Model IDs: `OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX` and `OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX`.
- Expected output: native 48 kHz, two-channel audio.
- The official ONNX CPU path is recommended first. Reference audio is optional; without it the official `Junhao` preset is used. Its ONNX workflow does not consume a reference transcript.
- Code and current model cards are Apache-2.0; commercial usage appears permitted by that label, subject to review.
- Sources: [repository](https://github.com/OpenMOSS/MOSS-TTS-Nano), [100M model card](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M), [ONNX model](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX).

```powershell
New-Item -ItemType Directory -Force .\.runtime\tts-experiments\upstream
git clone https://github.com/OpenMOSS/MOSS-TTS-Nano.git .\.runtime\tts-experiments\upstream\MOSS-TTS-Nano
py -3.12 -m venv .\.runtime\tts-experiments\venvs\moss-nano
.\.runtime\tts-experiments\venvs\moss-nano\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\moss-nano\Scripts\python.exe -m pip install -e .\.runtime\tts-experiments\upstream\MOSS-TTS-Nano
```

Run with its built-in voice:

```powershell
.\.runtime\tts-experiments\venvs\moss-nano\Scripts\python.exe .\experiments\tts_local\run_moss_tts_nano.py
```

Run voice cloning:

```powershell
.\.runtime\tts-experiments\venvs\moss-nano\Scripts\python.exe .\experiments\tts_local\run_moss_tts_nano.py --reference-audio .\.runtime\tts-experiments\references\speaker.wav --reference-text-file .\.runtime\tts-experiments\references\speaker.txt
```

## OmniVoice setup

- Official package: `omnivoice` (Python 3.10+); Python 3.11 is recommended here.
- Model ID: `k2-fsa/OmniVoice`.
- Expected output: 24 kHz mono according to the official Python example.
- The lab intentionally requires both a 3–10 second reference recording and its exact transcript, even though upstream can auto-transcribe or use non-cloning modes. CPU uses float32; float16 is selected only on CUDA.
- Code is Apache-2.0. Current model weights are CC-BY-NC and non-commercial according to the official model card, even though the code license differs; the card does not state a Creative Commons version. Commercial usage does not appear permitted by that weight license.
- Sources: [repository and package API](https://github.com/k2-fsa/OmniVoice), [official model card](https://huggingface.co/k2-fsa/OmniVoice).

```powershell
py -3.11 -m venv .\.runtime\tts-experiments\venvs\omnivoice
.\.runtime\tts-experiments\venvs\omnivoice\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\omnivoice\Scripts\python.exe -m pip install torch==2.8.0+cu128 torchaudio==2.8.0+cu128 --extra-index-url https://download.pytorch.org/whl/cu128
.\.runtime\tts-experiments\venvs\omnivoice\Scripts\python.exe -m pip install omnivoice
```

Run:

```powershell
.\.runtime\tts-experiments\venvs\omnivoice\Scripts\python.exe .\experiments\tts_local\run_omnivoice.py --reference-audio .\.runtime\tts-experiments\references\speaker.wav --reference-text-file .\.runtime\tts-experiments\references\speaker.txt
```

## Chatterbox V3 setup

- Official package: `chatterbox-tts`; Python 3.10+ (Python 3.11 recommended here).
- Model ID: `ResembleAI/chatterbox`, specifically `t3_mtl23ls_v3` through `t3_model="v3"`.
- Expected output: native 24 kHz mono. The official runtime applies its punctuation preparation and Perth neural watermark; both are reported.
- Reference audio is optional; the official built-in conditioning is used otherwise. A supplied transcript is hashed for cross-model identity but is not consumed by Chatterbox.
- Code and weights are MIT; commercial usage appears permitted by that label, subject to review.
- Sources: [repository](https://github.com/resemble-ai/chatterbox), [model card](https://huggingface.co/ResembleAI/chatterbox).

```powershell
py -3.11 -m venv .\.runtime\tts-experiments\venvs\chatterbox-v3
.\.runtime\tts-experiments\venvs\chatterbox-v3\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\chatterbox-v3\Scripts\python.exe -m pip install chatterbox-tts
```

Run without or with a prompt:

```powershell
.\.runtime\tts-experiments\venvs\chatterbox-v3\Scripts\python.exe .\experiments\tts_local\run_chatterbox_v3.py
.\.runtime\tts-experiments\venvs\chatterbox-v3\Scripts\python.exe .\experiments\tts_local\run_chatterbox_v3.py --audio-prompt .\.runtime\tts-experiments\references\speaker.wav --reference-text-file .\.runtime\tts-experiments\references\speaker.txt
```

## Piper setup

- Official package/API: `piper-tts` from `OHF-Voice/piper1-gpl`; Python 3.11 is recommended here.
- Default voice: `pl_PL-darkman-medium`, loaded from an existing `.runtime/piper/pl_PL-darkman-medium/**/pl_PL-darkman-medium.onnx` first.
- Expected output: 22,050 Hz mono for darkman.
- CPU is the intended baseline. `--device cuda` uses the official `use_cuda=True` load path and requires `onnxruntime-gpu`.
- Runtime code is GPL-3.0. The Piper voice repository is labeled MIT; the darkman card says its dataset is CC0 and that it was fine-tuned from the U.S. English lessac voice. Commercial status is not asserted here; review all applicable voice and base-model terms.
- Sources: [official runtime](https://github.com/OHF-Voice/piper1-gpl), [Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md), [voice repository](https://huggingface.co/rhasspy/piper-voices), [darkman card](https://huggingface.co/rhasspy/piper-voices/blob/main/pl/pl_PL/darkman/medium/MODEL_CARD).

```powershell
py -3.11 -m venv .\.runtime\tts-experiments\venvs\piper
.\.runtime\tts-experiments\venvs\piper\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\piper\Scripts\python.exe -m pip install piper-tts
```

Only if the model is not already present, download it with the official utility:

```powershell
.\.runtime\tts-experiments\venvs\piper\Scripts\python.exe -m piper.download_voices --download-dir .\.runtime\piper\pl_PL-darkman-medium pl_PL-darkman-medium
```

Run:

```powershell
.\.runtime\tts-experiments\venvs\piper\Scripts\python.exe .\experiments\tts_local\run_piper.py
```

## VoxCPM2 setup

- Official package: `voxcpm`; Python 3.10–3.12 (Python 3.11 recommended here), PyTorch 2.5+, CUDA 12+ for GPU.
- Model ID: `openbmb/VoxCPM2`.
- Expected output: native 48 kHz audio.
- Default, reference-only cloning, and “ultimate” cloning with the exact transcript are supported by the official API.
- Code and weights are Apache-2.0; upstream calls the release commercial-ready. Review the license before production use.
- This is a heavy 2B experimental runner. Do not run it automatically on the current GTX 1660 SUPER 6 GB, and do not claim it fits. CUDA OOM is reported; there is no silent CPU fallback.
- Sources: [repository and package API](https://github.com/OpenBMB/VoxCPM), [model card](https://huggingface.co/openbmb/VoxCPM2).

```powershell
py -3.11 -m venv .\.runtime\tts-experiments\venvs\voxcpm2
.\.runtime\tts-experiments\venvs\voxcpm2\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\voxcpm2\Scripts\python.exe -m pip install voxcpm
```

Run only after consciously choosing a suitable device:

```powershell
.\.runtime\tts-experiments\venvs\voxcpm2\Scripts\python.exe .\experiments\tts_local\run_voxcpm2.py --device cpu
```

Optional ultimate cloning:

```powershell
.\.runtime\tts-experiments\venvs\voxcpm2\Scripts\python.exe .\experiments\tts_local\run_voxcpm2.py --device cpu --reference-audio .\.runtime\tts-experiments\references\speaker.wav --reference-text-file .\.runtime\tts-experiments\references\speaker.txt
```

## MOSS-TTS-Realtime setup

- Official runtime: editable checkout of `OpenMOSS/MOSS-TTS`; Python 3.12 and Transformers 5.0.0 are recommended upstream.
- Model IDs: `OpenMOSS-Team/MOSS-TTS-Realtime` and `OpenMOSS-Team/MOSS-Audio-Tokenizer`.
- Expected output: 24 kHz from the official codec example.
- Code and weights are Apache-2.0; commercial usage appears permitted by that label, subject to review.
- This runner uses the official finite, non-streaming comparison call and writes one complete WAV. Upstream keeps streaming examples in `moss_tts_realtime/example_llm_stream_to_tts.py` and `example_multiturn_stream_to_tts.py`; this lab does not start a server or long-running stream.
- This is a heavy 1.7B model whose published latency measurements use an L20. Do not run it automatically on the current GTX 1660 SUPER 6 GB. CUDA OOM is reported without CPU fallback.
- Sources: [repository](https://github.com/OpenMOSS/MOSS-TTS), [official realtime model card](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Realtime), [upstream usage guide](https://github.com/OpenMOSS/MOSS-TTS/blob/main/docs/moss_tts_realtime_model_card.md).

```powershell
New-Item -ItemType Directory -Force .\.runtime\tts-experiments\upstream
git clone https://github.com/OpenMOSS/MOSS-TTS.git .\.runtime\tts-experiments\upstream\MOSS-TTS
py -3.12 -m venv .\.runtime\tts-experiments\venvs\moss-realtime
.\.runtime\tts-experiments\venvs\moss-realtime\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\tts-experiments\venvs\moss-realtime\Scripts\python.exe -m pip install --extra-index-url https://download.pytorch.org/whl/cu128 -e ".\.runtime\tts-experiments\upstream\MOSS-TTS[torch-runtime]"
```

Run only after consciously choosing a suitable device:

```powershell
.\.runtime\tts-experiments\venvs\moss-realtime\Scripts\python.exe .\experiments\tts_local\run_moss_tts_realtime.py --device cpu
```

## Reference audio

Create the ignored reference directory and place your own permitted recording and exact UTF-8 transcript there:

```powershell
New-Item -ItemType Directory -Force .\.runtime\tts-experiments\references
```

Suggested paths are `.runtime/tts-experiments/references/speaker.wav` and `.runtime/tts-experiments/references/speaker.txt`. Do not silently substitute another clip. OmniVoice requires both paths in this lab. MOSS Nano, Chatterbox V3, VoxCPM2, and MOSS Realtime accept optional reference audio; their current transcript behavior is stated in their setup sections and recorded in reports.

## Downloads, cache, and disk space

No setup or inference happens merely by running `--help`. Actual first inference may download model weights and can take substantial time. MOSS Nano and both heavy models may involve multiple model and codec repositories. VoxCPM2, MOSS Realtime, and their caches can consume many gigabytes.

Set Hugging Face's cache to the ignored laboratory directory before the first model load if desired:

```powershell
$env:HF_HOME = (Resolve-Path .\.runtime).Path + "\tts-experiments\cache\huggingface"
New-Item -ItemType Directory -Force $env:HF_HOME
```

This environment variable applies only to the current PowerShell session. Supertonic currently documents its separate `~/.cache/supertonic3` default. The MOSS Nano ONNX checkout defaults its assets under its checkout when no explicit model directory is supplied; that checkout is itself under `.runtime/tts-experiments/upstream/`.

## Output and manual comparison

Each successful run writes `benchmark.wav` and a same-name `.json` report under `.runtime/tts-experiments/outputs/<model>/`. Existing WAV or report files require `--overwrite`. A completed report is written only after a valid, non-empty WAV with positive duration and sample rate is inspected.

For manual comparison:

1. Generate every candidate from the unchanged benchmark and, for cloning, the same reference pair.
2. Confirm the input and reference SHA-256 fields match across reports.
3. Listen at the same playback volume without transcoding the files.
4. Compare pronunciation, punctuation, numbers, abbreviations, tempo, artifacts, and voice consistency.
5. Treat `real_time_factor` as local runtime information only; the scripts preserve native formats, so output rates and channels can differ.

Do not infer a quality ranking until samples have actually been generated and listened to locally.

## Troubleshooting

- **Missing dependency:** Use the environment named in the error and rerun that model's setup commands. Do not install model packages into the repository environment.
- **CUDA unavailable:** Confirm that the environment has the intended CUDA-enabled PyTorch or ONNX Runtime build. Use `--device cpu` only when that model's section identifies CPU as a deliberate option.
- **CUDA out of memory:** Close GPU applications or choose CPU explicitly if supported. VoxCPM2 and MOSS Realtime never silently fall back to CPU; do not run either automatically on the 6 GB GPU.
- **Missing reference audio:** Supply an existing permitted path under `.runtime/tts-experiments/references/`. OmniVoice also requires a non-empty exact transcript.
- **Missing Piper model:** Reuse an existing `pl_PL-darkman-medium.onnx` under `.runtime/piper/pl_PL-darkman-medium/`, run the documented official download command, or pass `--model-path`.
- **Unsupported Python version:** Recreate only that model's environment with the version documented in its section. MOSS environments use Python 3.12; the other examples use Python 3.11.
- **Unavailable official API:** Update the relevant official checkout and compare its current model card/API to the runner. Do not replace it with a guessed generic pipeline call. MOSS Nano and MOSS Realtime intentionally require checkout-local interfaces.
- **Model download/network failure:** Retry only after checking connectivity and free disk space. Preserve a partial cache for the upstream downloader to handle; do not add weights to Git.
- **Output already exists:** Review it, then rerun with `--overwrite` only when replacement is intentional.

## License reminder

This document reports upstream labels as observed during implementation and does not provide legal advice. Code and model weights can have different licenses, and voice datasets can add separate terms. OmniVoice weights are currently non-commercial according to the official model card. Review every upstream code, weight, voice, and dataset license again before production use or distribution.

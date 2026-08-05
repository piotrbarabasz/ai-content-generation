# Piper Polish voice catalog

This document mirrors the curated metadata in
`backend/app/providers/piper_catalog.py`. The catalog is the source of truth.

The Piper engine is licensed separately under `MIT`. Each voice model carries
its own model-license identifier, which must be reviewed independently.

## Curated voices

All catalog entries are Polish (`pl_PL`) and currently include only these five
voice keys:

| Voice key | Voice name | Quality | Sample rate | Source revision | Model license |
|---|---|---:|---:|---|---|
| `pl_PL-bass-high` | `bass` | high | 22050 Hz | `7c89b592e94392a56c789bd35a860fec66a6583f` | `Apache-2.0` |
| `pl_PL-darkman-medium` | `darkman` | medium | 22050 Hz | `e9ef9dd` | `CC0` |
| `pl_PL-gosia-medium` | `gosia` | medium | 22050 Hz | `e9ef9dd` | `CC0` |
| `pl_PL-mc_speech-medium` | `mc_speech` | medium | 22050 Hz | `441d4ac` | `CC0` |
| `pl_PL-mls_6892-low` | `mls_6892` | low | 16000 Hz | `5227e41` | `CC-BY-4.0` |

## Common source metadata

- Source repository: `rhasspy/piper-voices`
- Language: `pl_PL`
- Engine license identifier: `MIT`
- Required files for every entry:
  - model `.onnx`
  - model config `.onnx.json`
  - `MODEL_CARD`

## Voice details

### `pl_PL-bass-high`

- Voice name: `bass`
- Quality: `high`
- Expected sample rate: `22050 Hz`
- Source repository: `rhasspy/piper-voices`
- Source revision: `7c89b592e94392a56c789bd35a860fec66a6583f`
- Required files:
  - `pl/pl_PL/bass/high/pl_PL-bass-high.onnx`
  - `pl/pl_PL/bass/high/pl_PL-bass-high.onnx.json`
  - `pl/pl_PL/bass/high/MODEL_CARD`
- Checksums:
  - `pl/pl_PL/bass/high/pl_PL-bass-high.onnx` -> `427c7c0975ee21cea29db0f58f827883`
  - `pl/pl_PL/bass/high/pl_PL-bass-high.onnx.json` -> `0a121543c2a697ddb48a74bbdd0fbbe9`
  - `pl/pl_PL/bass/high/MODEL_CARD` -> `53b729f8209e4fc98d55c299055d79b5`
- Model license: `Apache-2.0`

### `pl_PL-darkman-medium`

- Voice name: `darkman`
- Quality: `medium`
- Expected sample rate: `22050 Hz`
- Source repository: `rhasspy/piper-voices`
- Source revision: `e9ef9dd`
- Required files:
  - `pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx`
  - `pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx.json`
  - `pl/pl_PL/darkman/medium/MODEL_CARD`
- Checksums:
  - `pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx` -> `27bf2d71e934b112657544fd0b100a7a`
  - `pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx.json` -> `1c13180312cca98cb75ca39b31972056`
  - `pl/pl_PL/darkman/medium/MODEL_CARD` -> `1a570e4294182ab00ca0e62f343f7279`
- Model license: `CC0`

### `pl_PL-gosia-medium`

- Voice name: `gosia`
- Quality: `medium`
- Expected sample rate: `22050 Hz`
- Source repository: `rhasspy/piper-voices`
- Source revision: `e9ef9dd`
- Required files:
  - `pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx`
  - `pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx.json`
  - `pl/pl_PL/gosia/medium/MODEL_CARD`
- Checksums:
  - `pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx` -> `ecf817530e575025166e454adde1f382`
  - `pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx.json` -> `82fe5f840c3af4c98e8a1430431ecdbd`
  - `pl/pl_PL/gosia/medium/MODEL_CARD` -> `e1355330fe5fab166e6f2e20af7e91e9`
- Model license: `CC0`

### `pl_PL-mc_speech-medium`

- Voice name: `mc_speech`
- Quality: `medium`
- Expected sample rate: `22050 Hz`
- Source repository: `rhasspy/piper-voices`
- Source revision: `441d4ac`
- Required files:
  - `pl/pl_PL/mc_speech/medium/pl_PL-mc_speech-medium.onnx`
  - `pl/pl_PL/mc_speech/medium/pl_PL-mc_speech-medium.onnx.json`
  - `pl/pl_PL/mc_speech/medium/MODEL_CARD`
- Checksums:
  - `pl/pl_PL/mc_speech/medium/pl_PL-mc_speech-medium.onnx` -> `a927e2f2c882bb40cbc2e5f3356ce19b`
  - `pl/pl_PL/mc_speech/medium/pl_PL-mc_speech-medium.onnx.json` -> `3f506e68bb9531b11e94e5f5dda5dd21`
  - `pl/pl_PL/mc_speech/medium/MODEL_CARD` -> `affe6073af7777237f73d0768103547e`
- Model license: `CC0`

### `pl_PL-mls_6892-low`

- Voice name: `mls_6892`
- Quality: `low`
- Expected sample rate: `16000 Hz`
- Source repository: `rhasspy/piper-voices`
- Source revision: `5227e41`
- Required files:
  - `pl/pl_PL/mls_6892/low/pl_PL-mls_6892-low.onnx`
  - `pl/pl_PL/mls_6892/low/pl_PL-mls_6892-low.onnx.json`
  - `pl/pl_PL/mls_6892/low/MODEL_CARD`
- Checksums:
  - `pl/pl_PL/mls_6892/low/pl_PL-mls_6892-low.onnx` -> `8590d8e979292ca35d20e6e123bfa612`
  - `pl/pl_PL/mls_6892/low/pl_PL-mls_6892-low.onnx.json` -> `7da3504b7726d6a7143a9265d9295fa1`
  - `pl/pl_PL/mls_6892/low/MODEL_CARD` -> `74ebc618d120896113449ad2f957b7a4`
- Model license: `CC-BY-4.0`

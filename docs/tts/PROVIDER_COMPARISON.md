# Provider Comparison Manifest

Use this manifest with `scripts/run-tts-provider-comparison.ps1` to compare the curated Polish TTS profiles in a repeatable order.

```json
{
  "version": 1,
  "seed": 20260805,
  "default_input_text_file": "../../backend/tests/fixtures/narrations/story_01_1min.txt",
  "profiles": [
    {
      "profile_id": "chatterbox-neutral",
      "label": "Chatterbox neutral",
      "provider": "chatterbox_v3",
      "settings": {
        "language_id": "pl"
      },
      "enabled_by_default": true,
      "requires_approved_reference": false
    },
    {
      "profile_id": "piper-pl_PL-bass-high",
      "label": "Piper pl_PL-bass-high",
      "provider": "piper",
      "settings": {
        "language_id": "pl",
        "model_key": "pl_PL-bass-high"
      },
      "enabled_by_default": true,
      "requires_approved_reference": false
    },
    {
      "profile_id": "piper-pl_PL-darkman-medium",
      "label": "Piper pl_PL-darkman-medium",
      "provider": "piper",
      "settings": {
        "language_id": "pl",
        "model_key": "pl_PL-darkman-medium"
      },
      "enabled_by_default": true,
      "requires_approved_reference": false
    },
    {
      "profile_id": "piper-pl_PL-gosia-medium",
      "label": "Piper pl_PL-gosia-medium",
      "provider": "piper",
      "settings": {
        "language_id": "pl",
        "model_key": "pl_PL-gosia-medium"
      },
      "enabled_by_default": true,
      "requires_approved_reference": false
    },
    {
      "profile_id": "piper-pl_PL-mc_speech-medium",
      "label": "Piper pl_PL-mc_speech-medium",
      "provider": "piper",
      "settings": {
        "language_id": "pl",
        "model_key": "pl_PL-mc_speech-medium"
      },
      "enabled_by_default": true,
      "requires_approved_reference": false
    },
    {
      "profile_id": "piper-pl_PL-mls_6892-low",
      "label": "Piper pl_PL-mls_6892-low",
      "provider": "piper",
      "settings": {
        "language_id": "pl",
        "model_key": "pl_PL-mls_6892-low"
      },
      "enabled_by_default": true,
      "requires_approved_reference": false
    },
    {
      "profile_id": "xtts-pl-reference",
      "label": "XTTS reference evaluation",
      "provider": "xtts_v2_eval",
      "settings": {
        "language_id": "pl",
        "model_variant": "xtts_v2",
        "approved_label": "consent-2026-08"
      },
      "enabled_by_default": false,
      "requires_approved_reference": true
    }
  ],
  "scoring_template": [
    "naturalness",
    "polish_pronunciation",
    "pace",
    "timbre",
    "expression",
    "artifacts"
  ]
}
```

## Human Scoring Template

Use a 1 to 5 scale for each criterion, where 1 is poor and 5 is strong.

| Criterion | 1 | 3 | 5 |
| --- | --- | --- | --- |
| Naturalness | Robotic or synthetic. | Mostly natural with some rough edges. | Sounds convincingly human. |
| Polish pronunciation | Frequent mistakes. | Occasional errors or stress issues. | Clear, idiomatic Polish pronunciation. |
| Pace | Too fast, too slow, or unstable. | Mostly steady with minor pacing drift. | Comfortable and consistent speaking pace. |
| Timbre | Harsh, thin, or unstable. | Acceptable but not distinctive. | Pleasant and stable voice color. |
| Expression | Flat or exaggerated. | Some expressiveness with uneven moments. | Appropriate emotional phrasing. |
| Artifacts | Noticeable clipping, glitches, or noise. | Minor artifacts that do not dominate. | Clean audio without distracting artifacts. |

## Run Notes

- Use the same normalized text for every profile.
- Run profiles sequentially.
- Provide an approved reference WAV before enabling XTTS.
- Keep generated evidence under ignored runtime directories.

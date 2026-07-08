# Provider Contracts

## LLMProvider
- Purpose: generate narrative text, outlines and summaries
- Methods: generate_text(prompt, context), generate_structured(prompt, schema)
- Errors: provider_unavailable, rate_limited, invalid_request

## TTSProvider
- Purpose: synthesize voiceover audio from text
- Methods: synthesize(text, voice_config)
- Errors: provider_unavailable, invalid_voice

## TranscriptionProvider
- Purpose: transcribe audio for timing and review
- Methods: transcribe(audio_ref)
- Errors: provider_unavailable, unsupported_audio

## CaptionProvider
- Purpose: generate captions or subtitle tracks from speech and scene data
- Methods: generate_captions(audio_ref, transcript_ref)
- Errors: provider_unavailable, invalid_input

## AssetProvider
- Purpose: provide visual assets for scene assembly
- Methods: find_assets(query), prepare_asset(asset_ref)
- Errors: provider_unavailable, asset_not_found

## VideoRendererProvider
- Purpose: render video from scenes and assets
- Methods: render(scene_plan, audio_ref, captions_ref)
- Errors: provider_unavailable, render_failed

## StorageProvider
- Purpose: persist and retrieve artifacts
- Methods: save_artifact(name, content, metadata), read_artifact(key), list_artifacts(prefix)
- Errors: storage_unavailable, write_failed

## PublishingProvider
- Purpose: publish exports to external platforms
- Methods: publish(export_bundle, target)
- Errors: provider_unavailable, publish_failed

Note: PublishingProvider is defined for future use but is not implemented in the MVP.

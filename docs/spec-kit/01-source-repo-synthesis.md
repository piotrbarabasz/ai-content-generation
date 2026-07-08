# 01 Source Repo Synthesis

## Summary

The two source repositories contribute complementary capabilities. The shorts repository is strong in visual production and timing. The long-form repository is strong in research-driven writing and narrative construction.

The unified app should treat these as two sides of the same workflow engine:
- the shorts repository contributes the production layer for scenes, timing and rendering
- the long-form repository contributes the research and writing layer for structure, script and QA

## Key Contribution Comparison

The shorts repository contributes:
- scene planning
- speech timing
- captions
- video rendering
- asset projection
- export

The long-form repository contributes:
- research
- ingestion
- RAG
- dossier
- outline
- script generation
- editing
- QA
- voiceover

## Synthesis Table

| area | shorts repo insight | long-form repo insight | target unified app decision |
| --- | --- | --- | --- |
| intake | accepts transcript and media inputs for short video assembly | accepts topic, source manifests and research inputs for long-form storytelling | Use a shared brief and input layer that supports transcript, topic, source manifest and media input |
| planning | provides scene segmentation and narrative planning for short-form pacing | provides narrative outline, segment planning and dossier-based structure | Use a shared planning layer with both scene-level and story-level planning modes |
| script generation | does not generate the script from scratch; relies on transcript input | generates long-form script drafts from research and plan artifacts | Support both human-supplied script and AI-generated script paths |
| voiceover | aligns audio timing to scenes and narrative beats | produces cleaned transcript and voiceover chunks for narration | Make voiceover optional and reusable across short and long-form workflows |
| visuals | selects and projects assets for rendering | has limited visual asset planning and no full render path | Add a shared asset planning and selection layer for both formats |
| captions | already has caption planning and ASS-style delivery | does not yet implement captions as a first-class module | Treat captions as a reusable optional module for video outputs |
| quality | has limited QA and mostly operational error handling | includes QA and critique stages | Make QA a standard checkpoint in the workflow engine |
| export | already supports video and subtitle export | supports transcript, QA and audio export | Standardize on a shared export bundle with manifest and artifact packaging |

## Unified App Direction

The unified application should not be built as two separate products. Instead, it should be a single engine that swaps modules based on the selected output type.

In practice, this means:
- short video workflows use planning, timing, captions and rendering modules
- long-form workflows use research, dossier, outline, script, QA and voiceover modules
- audio-only and script-only workflows skip the visual modules when not required

# D022 — Scene and prompt editor UI

## Implemented boundary

The project editor now includes a docked scene-visuals panel for the selected
saved section revision. It discovers the latest retained D013 acceptance for that
exact revision and displays one card per immutable scene with source text, visual
description and measured timing when a D013 timing set exists. A missing current
acceptance produces an empty explanatory state; the panel never invents a scene
plan or retimes scenes.

Each scene exposes its independent D015 prompt history and D016 image history.
Saving prompt text creates and explicitly selects a manual prompt revision.
Regeneration uses the selected prompt's pinned brief/style revisions, retains the
generated revision, and appends an explicit selection event only after successful
generation. Historical prompt variants can be selected explicitly. For a scene
without a selected prompt, host composition must provide an explicit brief/style
revision resolver; D022 does not infer a global or latest context.

Image import uses D016's bounded PNG/JPEG validation and then explicitly selects
the retained candidate. Image generation uses D017's request, D006 job and D040
candidate publication contracts. Cache hits and successful new candidates still
require a D016 image-selection event. Historical imported/generated variants stay
available and can be reselected. Display bytes are reread only after ownership,
checksum and measurement validation.

Prompt/image actions are scoped by scene ID. They do not invoke TTS, rewrite the
scene plan, modify another scene, or delete prior prompt/image/audio artifacts.
Failed providers, decoding, publication or stale selection tokens leave the prior
selection intact. An unsaved prompt draft prevents scene/section/project switching
and editor close so manual text is not silently discarded.

## Composition

`app.desktop.scene_composition.compose_scenes` constructs the existing D006,
D013, D015, D016, D017 and D040 adapters for an open project session. Providers
and identities remain explicit:

```python
from app.desktop.__main__ import main
from app.desktop.scene_composition import compose_scenes

def scene_factory(session):
    return compose_scenes(
        session,
        prompt_provider=prompt_provider,
        prompt_identity=prompt_identity,
        image_provider=image_provider,
        context_resolver=context_resolver,
    )

main(scene_factory=scene_factory)
```

Disabled providers produce clear action errors; there is no hidden mock, model
installation, automatic context choice or image fallback. D017's current provider
contract is synchronous and explicitly injected. D022 adds no local model runtime
or general background image-worker framework.

## Validation evidence

Offline Qt tests cover scene cards, timing/text display,
scene-local prompt edits, generation failure, image import/generation/selection,
draft preservation and editor-factory wiring. SQLite/storage integration tests use
the real D013–D017 adapters, deterministic structured/image fixtures, retained
audio bytes, provider failures, old-variant reselection, reopen and rendered image
display. No network, real image model or TTS provider is used.

- Focused D022/editor tests: **20 passed**.
- D013–D017 compatibility plus D022/editor tests: **152 passed**.
- Full `python -m pytest backend/tests`: **1477 passed, 11 skipped** in 320.82 s.
- Native Qt panel rendering was inspected at 720×760; cards, prompt controls,
  image preview area, variant controls and generation settings fit the dock.
- `compileall`, `pip check` and `git diff --check`: **PASS**.

The 11 skipped cases are the existing optional Windows link-capability tests. No
D022 test is skipped. All D022 tests are deterministic and offline.

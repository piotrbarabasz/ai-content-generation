# D065: Project editor shell, storyboard and export UX

D065 composes the existing D063/D064 planning workflow and D020-D023 media
panels into a desktop-first project workspace. The five primary tabs are Plan,
Script, Storyboard, Timeline and Export. Voice, scene planning and visual
controls remain available inside the Storyboard inspector; they still use their
existing services and coordinator-thread repository ownership.

The outline is the visible section navigator. Social uses a flat list of planned
sections, while Standard groups sections under collapsible plan groups. The
hidden legacy combo/list remain synchronized adapters for existing editing
commands. Selecting a scene in the Storyboard forwards its identity to the
existing ScenePanel. Drawing cards only reads retained scene data and selected
image bytes; thumbnails are scaled to 150 by 84 pixels and the card widgets keep
the decoded pixmaps for repaint.

The header stage summary comes from `PipelineDiagnostics`. When the editor has a
render-aware project service, Export readiness reflects the selected
`project:video_render` artifact. The export panel resolves that exact selection,
checks registered render evidence and file size, then verifies the full checksum
before Play, Open Folder or Save Copy. Copies stream into `QSaveFile` and become
visible only after a successful commit. Preview-cache and work-render files are
not considered final exports.

Advanced selective regeneration starts collapsed. Timeline and Preview retain
their existing panels. The project database schema and video-plan, script,
scene, image, timeline and render identities are unchanged.

## Verification

- Focused shell, Storyboard, Export, diagnostics, D063 and D064 regression tests
  passed; the 60-scene Qt smoke populates cards without provider calls.
- Focused shell, Storyboard and Export suite: 35 passed.
- Full backend test suite: 1,882 passed, 11 skipped.
- `git diff --check` passed; the project database schema stayed at version 1.
- No paid provider calls or persistent media outputs are used by these tests.
- Interactive `run_test_chatterbox.py` acceptance was not performed here: this
  environment does not provide native desktop interaction, and that launcher
  requires a configured local Chatterbox runtime. The launcher was left intact.

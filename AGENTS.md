# Working in AI Content Studio

- Product code is in `backend/app/`; tests are in `backend/tests/`.
- Read the relevant code and `docs/architecture/` before implementation. Future
  product work is in `docs/ROADMAP.md`; `docs/archive/` is historical context.
- Make the smallest coherent change. Preserve existing behavior and public
  contracts unless the task explicitly requires changing them.
- Add or update behavioral tests for functionality. Keep tests deterministic,
  isolated and offline; real external connections require an explicit reason.
- Keep the workflow engine independent of concrete providers. Isolate integrations
  behind provider contracts and composition factories; optional disabled modules
  must not require their providers.
- Keep `NarrativeSegment`, `RenderScene` and technical TTS chunks separate.
- Preserve rejected artifacts and approval history. Resolve paths through configured
  storage. Never commit secrets, private reference audio, model caches or outputs.
- Use Python 3.11+ and an isolated environment. Run focused tests, then
  `python -m pytest backend/tests` and `git diff --check` after changes.
- Do not use Spec Kit, Agent Graph, local autopilot or the former multi-agent
  orchestration unless the user explicitly requests it.

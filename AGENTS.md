# Working in AI Content Studio

- Product code is in `backend/app/`; tests are in `backend/tests/`.
- The accepted product direction is desktop-first; see
  `docs/decisions/0003-desktop-first-architecture.md`.
- Read the relevant code and `docs/architecture/` for implemented behavior.
  `docs/desktop/IMPLEMENTATION_PLAN.md` is the authoritative target and D### backlog;
  `docs/ROADMAP.md` is its high-level navigation; `docs/archive/` is historical.
- Implement one explicitly selected D### task at a time, honoring its dependencies,
  scope and acceptance criteria. Do not add unrelated work. If code evidence requires
  wider scope, document the reason and update the task boundary before implementing it.
  A task ID or milestone is not evidence that a dependency is implemented.
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

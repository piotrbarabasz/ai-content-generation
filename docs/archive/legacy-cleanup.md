# Legacy orchestration cleanup

Audit date: 2026-09-11. Baseline: `bdfd9c9aa82c126e54a03947ac1b3eee4720e847` on
`master`, clean tracked and untracked Git inventory. Work was performed directly
on `chore/remove-legacy-agent-orchestration`, without agents, autopilot or task loops.

## Plan recorded before deletion

The product is `backend/app/{domain,api,modules,providers,storage,tts,workflow}`.
The content workflow engine is application functionality and must stay. TTS smoke
and comparison commands are also product support tools. Experiments are optional
research, not automatically registered production providers.

The legacy development system consists of `.agents`, `.codex`, tracked `.specify`
files, task/epic validators, receipts, the local autopilot, its launchers, Git hooks,
and the agent-system CI workflow. All 264 tracked Python files were parsed for
imports. No product module imports this tooling. `tooling/process_runner.py` is
used only by removed validation/receipt/hook code and its own tests; the TTS tempo
processor uses `subprocess.run` directly. Both old process runners can therefore
be removed. `tts_smoke.py` and `tts_compare.py` remain.

There is one product path dependency: `ApiSettings.tts_preview_root` defaults to
`.specify/runtime/tts-previews`. Change it to `.runtime/tts-previews` and retain
explicit settings injection. Leave existing ignored data untouched; do not copy
private runtime state into the documentation or Git. Previous previews can be
regenerated or an existing caller can explicitly select its previous cache root.

Remove only tracked legacy files, preserving ignored local receipts, caches,
virtual environments, model assets and generated audio. Keep ignore rules for old
private state so those files cannot accidentally become additions. Disconnect the
local `core.hooksPath=.githooks` and `agent.python` settings installed by the old
hook installer. Do not touch unrelated Git configuration.

Replace agent CI with checkout, Python 3.11, editable install and full pytest.
Remove the SSH/Git-bundle Linux validation wrappers: they run the obsolete gates;
normal Linux testing remains in GitHub Actions. Simplify both developer setup
scripts to install and run product tests. Remove unused PyYAML from base dependencies.

## Documentation and tests

Rewrite current facts from the specification, plan, data model, contracts,
research and constitution into [architecture](../architecture/overview.md).
Do not carry forward nonexistent domain entities, planned database layers,
old provider signatures, or the outdated assertion that publishing is unimplemented.
Replace the old active task queue with [ROADMAP](../ROADMAP.md) and preserve every
task assessment in [legacy-task-audit](legacy-task-audit.md). Full original sources
remain recoverable from the baseline Git commit; they are not copied wholesale.

Keep source-repository insights and migration mapping documents as explicitly
historical references. Remove redundant `docs/spec-kit` drafts and checklists after
preserving their product gaps and invariants. Remove the Agent Graph proposal and
autopilot manual: they concern a different development tool, not this application.

Delete the tooling test subtree, process-runner integration tests, and static
agent-flow, risk-routing and epic-PR tests. Refactor mixed tests:

- `test_t075.py`: retain provider policy, English-first ADR and architectural guards;
  remove milestone/task status assertions.
- `test_secret_hygiene.py`: retain placeholder and secret checks; assert product
  runtime exclusions instead of mandatory agent paths.
- `test_t066.py`: retain isolated runtime checks; remove dependence on `agent.python`.
- `test_t069.py`: retain Piper catalog, provenance, setup and model hygiene tests;
  remove the extinct `specs` directory from the model scan.
- `test_t074.py` and `test_t089.py`: keep provider/selection document contract
  checks and use the new relative Markdown links in the documentation index.

Add two offline preview acceptance cases for the default application cache root
and an injected custom root, including persisted cache reuse through a fresh
service instance and WAV delivery.

## Evidence and limits

Before deletion, the product suite passed **496 tests**. Its command excluded the
tooling subtree, process-runner integration test and the three agent-only static
test files. No old orchestration entrypoint was executed.

All 89 task rows were checked, all 20 epic manifests said completed, but milestone
metadata disagreed: M001 remained active; M002, M003 and M008 remained planned.
These labels are historical evidence, not proof of product readiness.

Actual gaps include disconnected API execution, process-local API state, an
unsatisfied short-preset module dependency, reference-only mock video output,
estimated word timing, and localization decisions that only mutate memory.
They predate this cleanup and belong in the product roadmap. Passing tests prove
their existing bounded contracts, not a complete production application.

## Final verification

All final checks passed on Windows with Python 3.11.9. Full pytest used a freshly
created `.tmp/cleanup-ci-env` and an editable base install, without PyYAML, torch,
Chatterbox, Piper or XTTS installed. The environment resolved FastAPI 0.141.1,
Pydantic 2.13.5 and pytest 9.1.1. Optional real TTS and publishing were not executed.
The new Linux GitHub Actions workflow was inspected but not run remotely.

| Check | Command / evidence | Final result |
| --- | --- | --- |
| Base install | `py -3.11 -m venv .tmp/cleanup-ci-env`, then that interpreter's `python -m pip install -e .` | PASS |
| Focused cleanup coverage | Command below | 62 passed |
| Provider decision documentation | `python -m pytest backend/tests/unit/test_t074.py` | 3 passed |
| Full suite in clean environment | `python -m pytest backend/tests` | **497 passed, 0 failed, 0 skipped**, 13.08 seconds |
| Patch whitespace | `git diff --check` | PASS |
| Setup syntax | PowerShell parser; Git Bash `bash -n scripts/setup-dev.sh` | PASS |
| CI shape | Read-only YAML inspection: checkout, Python 3.11, editable install, full pytest | PASS |
| Remaining Python imports | AST inspection of 196 backend Python files | No retired orchestration imports |
| Documentation | Resolve local links in README, AGENTS, index, roadmap, architecture and audit | PASS |
| Task coverage | Exactly one numbered assessment for every T001–T089 | 89 of 89 |

Focused command:

```sh
python -m pytest backend/tests/static backend/tests/integration/test_t087.py backend/tests/integration/test_t089.py backend/tests/integration/test_tts_catalog_api.py backend/tests/unit/test_t069.py
```

Intermediate failures were repaired without changing product behavior: document
tests expected the old index's repository-relative text instead of actual relative
Markdown links; the newly added preview tests initially expected HTTP 201 whereas
the existing endpoint returns HTTP 200. The first full clean-environment run was
496 passed / 1 failed on the remaining documentation-link assertion. The final
full result above supersedes those runs. A configured secondary package index had
DNS retries during installation; packages installed successfully from PyPI.

The count changed from baseline 496 to 497 because one milestone-status-only test
was removed and two preview storage/reload cases were added. All other product
behavioral coverage remains. Development-tool-only tests were excluded from the
baseline and deleted with their tooling, not counted as lost product coverage.

The tracked cleanup removes 174 legacy files and relocates six historical source
documents. Ignored pre-existing runtime data and virtual environments were left
untouched. The two old local hook config keys were removed. No commit, push,
merge, PR, deployment or branch-protection change was performed. HEAD remains
the baseline commit. See the [complete file inventory](cleanup-file-inventory.md).

The repository is ready for direct application development. Completing its
production HTTP/video path is separate product work explicitly captured in the
roadmap; no agent system is required for that work.

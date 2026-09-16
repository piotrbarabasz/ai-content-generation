# D020 — Project and section editor

## Boundary and authorization

On 2026-09-16 the user explicitly authorized development of D020 with the D002
clean-Windows and human playback gate deferred. D002 remains Partial. This
exception allows editor development/testing; it does not establish installer,
clean-machine or playback acceptance. D021 is not part of this implementation.

## Behavior and composition

`app.desktop.editor.ProjectEditor` presents a project create/open screen, ordered
multi-select section list, title/role/text fields, append/save/discard, split at
cursor, merge selected and move up/down actions. Ctrl/Shift selects multiple
sections for merge; D038 validates adjacency. Merge uses the displayed role as
the explicit resulting role and the existing two-newline separator policy.

Widgets delegate editorial validation to D014, D038 and D039 and persistence to
D003. SQLite composition is confined to `app.desktop.__main__.LocalProjects`.
Widget imports do not load storage, providers, TTS, HTTP or model runtimes.
No database schema or media selection changes are made.

Draft changes block section/project switching, structural operations and closing.
Save or explicit Discard draft releases that guard. Invalid input, save failure,
stale revision and failed project opening preserve the draft and valid saved
state. Discard refreshes the active snapshot, allowing recovery after a stale
revision. Saving an unchanged selected section performs no write. Qt cursor
offsets are translated from UTF-16 to original Python source positions, preserving
emoji and CRLF boundaries for D038.

Structured generation requires an injected D014 provider contract. The default
development entrypoint enables manual editing and disables generation with an
explanation when no provider is configured. Product composition may call
`main(provider=...)` or construct `ProjectEditor(LocalProjects(), provider=...)`;
no mock provider is silently substituted and no credentials are collected here.
Existing sections require explicit replacement confirmation; all historical
snapshots remain retained.

The provider runs in a QThread against a captured immutable script and a temporary
session port with no SQLite connection. D014 validates and builds the proposed
revision there. The GUI thread alone commits it with the captured expected active
revision. Late/conflicting and invalid results cannot replace valid saved state.
Editing, project switching and closing are blocked until the worker finishes;
the request text remains available on failure. Provider cancellation/timeouts are
not implemented by this screen: an injected provider must bound its calls. Heavy
model runtimes remain behind their own provider/process composition.

## Run

From the repository root, using the isolated Python 3.11 environment:

```powershell
.venv-ci311/Scripts/python.exe -m pip install -e '.[desktop]'
$env:PYTHONPATH = 'backend'
.venv-ci311/Scripts/python.exe -m app.desktop
```

Select a new folder and choose Create project, then enter a section title/text
and Save. New section starts another draft. Open project accepts a folder already
containing a D003 project. Folder ownership/locking errors appear in the status
line. The source entrypoint is distinct from the D002 packaging spike; deployment
of the final editor remains D041.

## Validation

**PASS (2026-09-16), under the explicitly authorized D002 gate deferral.**
Tests use real Qt widgets with fake application/provider
ports and temporary SQLite repositories; all generation/media data is synthetic
and offline. The desktop extra is optional: environments without it skip this Qt
module. Acceptance validation must install the extra and run the module explicitly.

Native Windows Qt rendering was inspected at 1000x720: labels, controls, section
text and disabled generation action render correctly. The offscreen Qt plugin
renders missing font glyphs on this host; functional offscreen tests pass. This
inspection is not evidence for D002 clean-machine/playback acceptance.

Environment: Windows developer host, isolated CPython 3.11.9, PySide6/Qt 6.11.2.

| Check | Result |
| --- | --- |
| D020 Qt module | 12 cases passed, including fake ports and SQLite user-flow smoke |
| Focused D020/D014/D038/D039/D003/D002 suite | 86 passed in 8.29s |
| Full `python -m pytest backend/tests` | 1447 passed, 11 skipped in 283.50s; existing optional D044 skips |
| `pip check`, compileall, `git diff --check` | PASS |
| Plan scope | Only D020 block changed; D002 remains Partial |

| Acceptance | Result and evidence |
| --- | --- |
| Create/open/edit/split/merge/reorder and reopen with stable revisions | PASS: real Qt button interactions and temporary SQLite preserve source history, selected identities and reordered values after reopen |
| Errors preserve unsaved text and valid saved state | PASS: dirty navigation/close/switch guards, invalid saves, fake disk/open failures, stale edit/generation tokens and invalid structured output |
| UI uses services without provider imports | PASS: injected application/provider ports and fresh-process import check; generation cannot pass SQLite connections to the worker |

Focused command:

```text
python -m pytest backend/tests/integration/test_project_editor.py backend/tests/integration/test_structured_script.py backend/tests/integration/test_section_editing.py backend/tests/integration/test_section_ordering.py backend/tests/integration/test_durable_project.py backend/tests/unit/test_desktop_spike.py
```

Changed files: `backend/app/desktop/editor.py`, `backend/app/desktop/__main__.py`,
`backend/tests/integration/test_project_editor.py`, `pyproject.toml`,
`docs/architecture/api-and-storage.md`, `docs/desktop/IMPLEMENTATION_PLAN.md` and
this evidence file. Branch: `feat/d020-project-section-editor`.

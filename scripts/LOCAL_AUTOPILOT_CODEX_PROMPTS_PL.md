# Lokalny Autopilot dla AI Content Studio — prompty do Codex CLI

## Cel

Zbudować lokalną aplikację Windows w `tkinter`, która po wybraniu epica albo milestone'u:

1. sprawdza repozytorium i zależności,
2. aktywuje epic po świadomym kliknięciu użytkownika,
3. tworzy branch,
4. wykonuje taski kolejno przez lokalny Codex CLI,
5. uruchamia walidacje i review,
6. tworzy jeden commit na task,
7. robi push,
8. tworzy draft Pull Request przez lokalny GitHub CLI,
9. zatrzymuje się przed merge,
10. pozwala wznowić milestone po ręcznym merge.

Autopilot **nigdy nie wykonuje automatycznego merge ani deploymentu**.

## Planowana struktura

```text
backend/app/tooling/local_autopilot/
  __init__.py
  __main__.py
  models.py
  config.py
  process_runner.py
  repository.py
  workstreams.py
  codex_adapter.py
  github_adapter.py
  task_pipeline.py
  epic_pipeline.py
  milestone_pipeline.py
  state_store.py
  controller.py
  ui.py

backend/tests/unit/tooling/local_autopilot/
  test_models.py
  test_state_store.py
  test_process_runner.py
  test_repository.py
  test_workstreams.py
  test_codex_adapter.py
  test_github_adapter.py
  test_task_pipeline.py
  test_epic_pipeline.py
  test_milestone_pipeline.py
  test_controller.py

scripts/
  run-local-autopilot.ps1
  run-local-autopilot.cmd

.specify/autopilot.yml
```

## Zasady

- Każdy prompt wykonuj osobno.
- Po każdym promptcie sprawdź diff i testy.
- Wszystkie polecenia Python mają używać interpretera z `git config --local --get agent.python`.
- Nie używaj `shell=True`.
- Każdy proces ma timeout.
- Testy nie mogą uruchamiać prawdziwego Codexa, GitHuba ani sieci.
- Każdy plik tekstowy ma kończyć się dokładnie jednym znakiem nowej linii.
- Podczas budowy autopilota Codex nie robi pushu ani PR, chyba że dany prompt jawnie tego wymaga.

---

# PROMPT 0 — audyt i plan

```text
Pracujesz na branchu feat/local-autopilot-ui w repozytorium ai-content-generation.

Wykonaj wyłącznie read-only audit przed implementacją lokalnego autopilota.

Cel:
Lokalna aplikacja Windows w tkinter. Użytkownik wybiera scope Epic/Milestone, ID, tryb Full/Stop before push i klika Start. Aplikacja deterministycznie steruje lokalnym Git, Codex CLI i GitHub CLI. Codex implementuje pojedynczy task, ale nie steruje sam całym procesem Git.

Sprawdź:
1. backend/app/tooling;
2. istniejące skille speckit;
3. format manifestów milestone/epic;
4. preflight, finalize, review i closer;
5. git config agent.python;
6. process runner i testy tooling;
7. .gitignore dla .specify/runtime;
8. pyproject.toml;
9. dostępność tkinter;
10. rzeczywiste wyjście: codex --help, codex exec --help, gh --version, gh auth status.

Nie loguj sekretów. Nie zmieniaj plików.

Raport:
AUDIT_STATUS:
PINNED_PYTHON:
TKINTER_AVAILABLE:
CODEX_CLI:
NON_INTERACTIVE_COMMAND:
GH_CLI:
PROPOSED_FILES:
STATE_MACHINE:
TEST_STRATEGY:
BLOCKERS:
```

---

# PROMPT 1 — modele, konfiguracja i state store

```text
Zaimplementuj fundament lokalnego autopilota.

Zakres:
- backend/app/tooling/local_autopilot/__init__.py
- backend/app/tooling/local_autopilot/models.py
- backend/app/tooling/local_autopilot/config.py
- backend/app/tooling/local_autopilot/state_store.py
- backend/tests/unit/tooling/local_autopilot/test_models.py
- backend/tests/unit/tooling/local_autopilot/test_state_store.py
- .specify/autopilot.yml

Wymagania:
1. Enumy: ScopeType(epic,milestone), RunMode(full,stop_before_push), RunStatus(idle,preflight,activating,branching,task_running,task_validating,task_committing,epic_review,pushing,pr_creating,waiting_for_merge,closing,completed,failed,cancelled).
2. Dataclasses: AutopilotRequest, AutopilotRun, CommandResult, TaskResult, PullRequestInfo.
3. Stan atomowo do .specify/runtime/autopilot/<run_id>.json.
4. Bez sekretów i pełnego env.
5. Walidacja ID: E\d{3}, M\d{3}.
6. .specify/autopilot.yml: auto_commit true, auto_push true, create_draft_pr true, auto_merge false, deploy false, max_repair_cycles 2, max_tasks_per_run 20, command_timeout_seconds 180, codex_timeout_seconds 3600, closure_mode pull_request.
7. Bez nowych zależności.

Walidacja przypiętym Pythonem, testy nowych modułów, git diff --check.
Nie wykonuj commita ani pushu.

Raport:
FILES_CHANGED:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): add state and configuration models`

---

# PROMPT 2 — bezpieczny process runner i Git

```text
Dodaj deterministyczną warstwę procesów i lokalnego Git.

Zakres:
- backend/app/tooling/local_autopilot/process_runner.py
- backend/app/tooling/local_autopilot/repository.py
- backend/tests/unit/tooling/local_autopilot/test_process_runner.py
- backend/tests/unit/tooling/local_autopilot/test_repository.py

Process runner:
- subprocess bez shell=True;
- args jako lista;
- stdout/stderr osobno;
- timeout i zakończenie drzewa procesu na Windows;
- jawne cwd;
- redakcja token/secret/password/api_key/authorization;
- anulowanie przez threading.Event;
- wstrzykiwany runner.

Repository:
- root repo, status, wymaganie clean tree;
- switch master i pull --ff-only;
- tworzenie brancha bez resetowania istniejącego;
- HEAD, changed/staged/untracked;
- stage tylko allowlisty;
- diff --check i diff --cached --check;
- commit z niepustą wiadomością;
- clean tree po commicie;
- push -u origin branch;
- bez force-push, reset --hard, rebase, stash i merge;
- normalizacja dokładnie jednej nowej linii na EOF tylko dla tekstowych plików allowlisty.

Testy bez sieci. Nie wykonuj commita ani pushu.

Raport:
FILES_CHANGED:
SAFETY_INVARIANTS:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): add safe local git operations`

---

# PROMPT 3 — workstreamy i kolejność tasków

```text
Zaimplementuj odczyt milestone'ów, epiców i tasków.

Zakres:
- backend/app/tooling/local_autopilot/workstreams.py
- backend/tests/unit/tooling/local_autopilot/test_workstreams.py

Wymagania:
- wykorzystaj istniejące parsery, gdy są bezpieczne;
- funkcje list/get milestone/epic, validate dependencies, activate_epic_with_human_authorization, list_epic_tasks, next_dependency_ready_task, all_epic_tasks_complete, next_ready_epic_for_milestone;
- planned -> active tylko przy human_authorized=True;
- nigdy completed -> active;
- nie zmieniaj checkboxów;
- kolejność zależności, nie tylko numerów;
- niejednoznaczność = stop;
- minimalny diff manifestu.

Testy: E002 zależy od completed E001, blokada zależności, T007 przed T008, aktywacja tylko po zgodzie, kolejny epic milestone'u.
Nie wykonuj commita ani pushu.

Raport:
FILES_CHANGED:
DEPENDENCY_RULES:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): add workstream selection logic`

---

# PROMPT 4 — adapter lokalnego Codex CLI

```text
Zaimplementuj adapter lokalnego Codex CLI.

Zakres:
- backend/app/tooling/local_autopilot/codex_adapter.py
- backend/tests/unit/tooling/local_autopilot/test_codex_adapter.py

Najpierw sprawdź rzeczywiście zainstalowane: codex --help i codex exec --help. Nie zakładaj nieobsługiwanych flag.

Wymagania:
- użyj ProcessRunner;
- wykryj brak CLI i brak trybu nieinteraktywnego;
- uruchamiaj w root repo;
- timeout i cancel;
- bez shell=True i bez credentiali w logach;
- prompt wymaga dokładnie jednego taska, agent.python, speckit-loop, bez commita/pushu/PR;
- wynik kończy się blokiem AUTOPILOT_RESULT_JSON;
- parser pobiera ostatni poprawny blok;
- brak JSON nie oznacza sukcesu;
- pipeline później sam weryfikuje Git i testy.

Testy bez prawdziwego Codexa.
Nie wykonuj commita ani pushu.

Raport:
FILES_CHANGED:
DETECTED_CODEX_COMMAND:
RESULT_PROTOCOL:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): integrate local Codex CLI`

---

# PROMPT 5 — adapter GitHub CLI

```text
Zaimplementuj adapter lokalnego gh.

Zakres:
- backend/app/tooling/local_autopilot/github_adapter.py
- backend/tests/unit/tooling/local_autopilot/test_github_adapter.py

Funkcje:
- validate_auth
- find_pr(base,head)
- create_draft_pr(base,head,title,body)
- get_pr_status
- is_pr_merged
- open_pr_in_browser

Zasady:
- lokalny gh, nie GitHub App Codexa;
- ProcessRunner, bez shell=True;
- PR idempotentny, bez duplikatów;
- zawsze draft;
- nigdy approve/merge/close;
- machine-readable JSON, gdy dostępny;
- testy fake runner, bez sieci.

Nie wykonuj commita ani pushu.

Raport:
FILES_CHANGED:
GH_AUTH_BEHAVIOR:
PR_IDEMPOTENCY:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): add GitHub CLI draft PR support`

---

# PROMPT 6 — pipeline pojedynczego taska

```text
Zaimplementuj deterministyczny pipeline jednego taska.

Zakres:
- backend/app/tooling/local_autopilot/task_pipeline.py
- backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py

Przebieg:
1. clean tree;
2. aktywny epic i branch;
3. preflight przypiętym Pythonem;
4. CodexAdapter dla jednego taska;
5. sprawdzenie zmian względem baseline;
6. allowlista taska;
7. tylko właściwy checkbox przez closer;
8. validation commands;
9. git diff --check;
10. deterministyczna naprawa whitespace/EOF tylko w allowliście i tylko raz;
11. task [X];
12. stage allowlisty i właściwego tasks.md;
13. diff --cached --check;
14. jeden commit: <type>(T###): <title>;
15. clean tree;
16. TaskResult do state store.

Repair maksymalnie max_repair_cycles i tylko dla bieżącego taska.
Testy: happy path, scope drift, zły checkbox, test fail, whitespace repair, commit fail, dirty tree, cancel, repair limit.
Nie wykonuj pushu ani PR.

Raport:
FILES_CHANGED:
TASK_PIPELINE_STAGES:
STOP_CONDITIONS:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): automate one task pipeline`

---

# PROMPT 7 — pipeline epica

```text
Zaimplementuj pipeline całego epica.

Zakres:
- backend/app/tooling/local_autopilot/epic_pipeline.py
- backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py

Przebieg:
1. clean repo;
2. master + pull --ff-only;
3. manifest i completed dependencies;
4. przy planned i human_authorized: utwórz branch, planned -> active, commit aktywacji;
5. istniejącego brancha nie resetuj;
6. zapisz active-epic;
7. pętla next_dependency_ready_task;
8. TaskPipeline dla każdego taska, sekwencyjnie;
9. required_checks;
10. epic review;
11. nie używaj pre-commit finalizera na późniejszym commitowanym HEAD jako jedynego dowodu;
12. weryfikuj commity tasków, późniejsze zmiany i scope drift;
13. review receipt;
14. stop_before_push kończy tutaj;
15. full: push i draft PR, status waiting_for_merge;
16. bez merge.

Testy: E002 T007->T008, aktywacja, resume, dependency fail, task fail, review fail, stop before push, push fail, istniejący/nowy PR.
Bez prawdziwej sieci.

Raport:
FILES_CHANGED:
EPIC_PIPELINE:
REVIEW_EVIDENCE_RULE:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): automate epic execution to draft PR`

---

# PROMPT 8 — milestone i resume

```text
Zaimplementuj milestone pipeline.

Zakres:
- backend/app/tooling/local_autopilot/milestone_pipeline.py
- backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py

Zasady:
- wybierz pierwszy nie-completed epic z completed dependencies;
- wykonaj dokładnie jeden epic do draft PR;
- zatrzymaj na waiting_for_merge;
- resume sprawdza gh, czy PR merged;
- po merge aktualizuje master i wykonuje bezpieczne close;
- domyślnie tworzy osobny bookkeeping branch i draft PR dla completed;
- nigdy direct push do master;
- closed bez merged nie jest sukcesem;
- dla squash/rebase użyj autorytatywnych danych gh;
- milestone completed dopiero po wszystkich epicach i kryteriach;
- respektuj max_tasks_per_run.

Testy: wybór epica, waiting, closed-not-merged, merged, closure PR, kolejny epic, koniec milestone'u, resume po restarcie.
Bez prawdziwej sieci.

Raport:
FILES_CHANGED:
MILESTONE_FLOW:
CLOSURE_POLICY:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): add resumable milestone workflow`

---

# PROMPT 9 — controller i UI tkinter

```text
Zaimplementuj controller i małe okno Windows w tkinter.

Zakres:
- backend/app/tooling/local_autopilot/controller.py
- backend/app/tooling/local_autopilot/ui.py
- backend/app/tooling/local_autopilot/__main__.py
- backend/tests/unit/tooling/local_autopilot/test_controller.py
- backend/tests/unit/tooling/local_autopilot/test_ui.py
- scripts/run-local-autopilot.ps1
- scripts/run-local-autopilot.cmd

UI:
- repo path + Browse;
- Scope Epic/Milestone;
- ID z manifestów;
- Mode Full/Stop before push;
- Create draft PR;
- Start, Stop, Resume, Open PR, Open logs;
- branch, status, epic, task, progress, last commit, PR;
- progress bar i scroll log;
- komunikaty końcowe.

Threading:
- jeden worker thread;
- queue + root.after;
- tylko UI thread modyfikuje widgety;
- Stop ustawia cancellation event;
- zamknięcie podczas pracy wymaga potwierdzenia.

Bezpieczeństwo:
- ekran potwierdzenia pokazuje repo/scope/ID/commit/push/PR oraz AUTO MERGE: NO;
- Start = human_authorized;
- merge i deploy wyłączone;
- logi bez sekretów.

Launcher:
- PS1 rozwiązuje agent.python i Python >=3.11;
- uruchamia -m backend.app.tooling.local_autopilot;
- CMD działa dwuklikiem.

Testy UI bez trwałego okna, przez fake view/root.
Nie wykonuj commita, pushu ani PR.

Raport:
FILES_CHANGED:
LAUNCH_COMMAND:
UI_FEATURES:
THREADING_MODEL:
TESTS:
DIFF_CHECK:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `feat(autopilot): add Windows desktop control panel`

---

# PROMPT 10 — hardening i testy integracyjne

```text
Wykonaj hardening lokalnego autopilota.

Zakres:
- backend/tests/unit/tooling/local_autopilot/
- minimalne poprawki backend/app/tooling/local_autopilot/
- docs/local-autopilot.md

Dodaj symulacyjne E2E z fake Codex, fake gh i tymczasowym repo:
- E002 planned->active->T007->commit->T008->commit->review->push->draft PR;
- resume po restarcie;
- cancel podczas Codex;
- timeout;
- niepoprawny result JSON;
- scope drift;
- blank EOF;
- pre-commit failure;
- push failure;
- gh 403/brak auth;
- istniejący PR;
- closed not merged;
- milestone po merge;
- brak agent.python/Python/tkinter/Codex/gh;
- dirty tree;
- master bez fast-forward.

Wymagania: bez shell=True, force push, auto merge, deploy, sieci w testach; każdy proces timeout; FAIL zostawia resumable state; logi redagowane; runtime niecommitowany.

Uruchom pełny pytest, tooling tests, workstream_validation, repository_checks --mode task-metadata i git diff --check.
Nie wykonuj commita ani pushu.

Raport:
FILES_CHANGED:
SCENARIOS_COVERED:
FULL_TEST_RESULT:
TOOLING_RESULT:
WORKSTREAM_RESULT:
DIFF_CHECK:
KNOWN_LIMITATIONS:
SAFE_TO_COMMIT:
SUGGESTED_COMMIT:
```

Commit: `test(autopilot): harden local epic and milestone flows`

---

# PROMPT 11 — finalny read-only review

```text
Wykonaj finalny, read-only review brancha feat/local-autopilot-ui względem master.

Nie modyfikuj plików.

Sprawdź architekturę, brak shell=True, timeouty, redakcję sekretów, brak auto merge/deploy, bezpieczeństwo Git, resume, Windows/tkinter, brak sieci w testach, agent.python, zgodność z lokalnym codex --help i gh, scope drift, testy i dokumentację dwukliku.

Uruchom przypiętym Pythonem pełny pytest, workstream_validation, repository_checks --mode task-metadata i git diff --check.

Raport:
FINAL_VERDICT:
BLOCKING_ISSUES:
NON_BLOCKING_ISSUES:
SECURITY_FINDINGS:
GIT_SAFETY:
CODEX_INTEGRATION:
GH_INTEGRATION:
WINDOWS_UI:
TEST_RESULT:
CHANGED_FILES:
PR_TITLE:
PR_BODY:
SAFE_TO_PUSH:
SAFE_TO_CREATE_PR:
```

Tytuł PR: `feat: add local epic and milestone autopilot`

---

# Uruchamianie po implementacji

Dwuklik:

```text
scripts\run-local-autopilot.cmd
```

Pierwszy test:

1. Scope `Epic`.
2. ID testowego epica.
3. Mode `Stop before push`.
4. Sprawdź commity i logi.
5. Dopiero później użyj `Full autopilot`.
6. Merge zawsze ręczny.

---

# PROMPT COMMIT — używaj po każdym zakończonym etapie

Po raporcie `SAFE_TO_COMMIT: yes` wklej do tego samego czatu Codexa:

```text
Wykonaj wyłącznie finalną walidację i commit zmian z właśnie zakończonego etapu.

1. Sprawdź git status --short i diff.
2. Upewnij się, że zmienione są tylko pliki zadeklarowane w poprzednim promptcie.
3. Użyj interpretera z git config --local --get agent.python.
4. Uruchom testy wskazane w raporcie oraz git --no-pager diff --check.
5. Dodaj do staged wyłącznie pliki tego etapu.
6. Uruchom git --no-pager diff --cached --check.
7. Wykonaj commit dokładnie z wiadomością SUGGESTED_COMMIT z poprzedniego raportu.
8. Nie wykonuj pushu, PR, merge ani kolejnego etapu.
9. Potwierdź, że working tree po commicie jest czysty.

Raport:
COMMIT_SHA:
COMMIT_MESSAGE:
FILES_COMMITTED:
VALIDATION:
WORKTREE_CLEAN:
SAFE_FOR_NEXT_PROMPT:
```

Dzięki temu podczas budowy autopilota nie musisz ręcznie wykonywać commitów w PowerShellu. Push całego brancha wykonaj dopiero po finalnym review.

---

# PROMPT 12 — push i draft PR dla autopilota

Uruchom dopiero po `FINAL_VERDICT: PASS` i `SAFE_TO_PUSH: yes`:

```text
Opublikuj zakończony branch feat/local-autopilot-ui.

1. Wymagaj czystego working tree.
2. Potwierdź aktualny branch feat/local-autopilot-ui.
3. Uruchom końcowo git --no-pager diff --check oraz pełny pytest przypiętym interpreterem agent.python.
4. Wykonaj git push -u origin feat/local-autopilot-ui.
5. Użyj lokalnego GitHub CLI gh, nie integracji GitHub Codexa.
6. Sprawdź, czy PR z tego brancha do master już istnieje.
7. Jeżeli nie istnieje, utwórz draft PR:
   title: feat: add local epic and milestone autopilot
   body: użyj PR_BODY z finalnego review.
8. Nie wykonuj approve, merge, close ani deploymentu.

Raport:
BRANCH:
HEAD_SHA:
PUSH_STATUS:
PR_NUMBER:
PR_URL:
DRAFT_STATUS:
MANUAL_ACTION_REQUIRED: review and merge
```

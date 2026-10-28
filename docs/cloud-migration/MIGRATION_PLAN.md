# Proponowany plan migracji i backlog CLOUD

Status: **PROPOSED**, 2026-10-28. Autorytatywny dla migracji dopiero po CLOUD-001. D### pozostaje planem desktopu; historycznych zadań nie kopiować, nie renumerować i nie oznaczać ukończonych na podstawie audytu. Prośba o dokumentację nie wybiera zadania implementacyjnego.

## Zasady wykonania

Jedna sesja wybiera jeden ID, czyta audyt/kontrakty i sprawdza implementację zależności. Nowe ścieżki w kartach są propozycją. Przy większym zakresie najpierw podzielić kartę i zaktualizować boundary. Bez Spec Kit, Agent Graph i delegacji. Status zadania nie zastępuje dowodu zależności.

Definicja ukończenia każdej karty kodowej: opisane niżej deterministyczne offline behavioral tests, focused suite, potem `python -m pytest backend/tests` w izolowanym Python 3.11+ i `git diff --check`. Linux dla cloud, Windows dla desktop/worker. Zachować public contracts, history/rejected/approvals, source media i odrębność NarrativeSegment/RenderScene/TTS chunks. Nowe provider tests przez fakes. Realne połączenia tylko w jawnie zatwierdzonej bramce. Baseline failures dokumentować; nie naprawiać unrelated kodu w tej samej karcie.

Rollback domyślnie odłącza nowy adapter/entrypoint lub wraca do zgodnego binary. Nie usuwa artefaktów i approvals. Restore zawsze do nowej lokalizacji; additive schema i compatibility tests. Brak masowego przenoszenia pakietów.

S/M/L: około 0.5–1 / 1–3 / 3–5 dni skupionej pracy po zależnościach, bez operator waits i niespodzianek środowiska. To złożoność, nie termin. Większe niż L dzielić przed kodowaniem. Ryzyko: niskie/średnie/wysokie.

## Milestones i krytyczna ścieżka

| Milestone | Karty | Bramka |
| --- | --- | --- |
| M0 — decyzje/kontrakty | 001–002 | Owner zatwierdził authority i granice |
| M1 — headless | 003–010 | Offline long+3 portrait, bez Qt/GPU, restart equivalence i desktop bez regresji |
| M2 — durable API/web lokalnie | 011–016 | Fakes/emulatory, 202 operations, auth/review UI i trwałość po restarcie |
| M3 — GCP | 017–022 | Jedno private środowisko, mock manual Job/GCS/QA; Scheduler paused |
| M4 — Windows worker | 023–028 | Secure handoff/image SR/final render i restart PC bez browsera |
| M5 — publishing | 029–032 oraz 031A | OAuth/resumable private upload, thumbnail/SRT, brak duplikacji; scheduling gated |
| M6 — produkcja | 033–039 | Cost/recovery/restore/release proofs i owner acceptance 1+3 |
| Później | 040 | Opcjonalny Drive; nie blokuje MVP |

Pierwszy lokalny pionowy przekrój: CLOUD-010. Krótkie playable fixtures long+3 oraz osobny 600-sekundowy timing fixture; pełny 10-min encode w benchmarku, nie każdy CI run. Pierwszy użyteczny cloud slice: CLOUD-021, manual request → batch → preview/3 Shorts/QA → GCS → dashboard. Transitional cloud final FHD działa bez workera, ale nie dowodzi neural SR.

Critical path z merge prerequisites: 001→002→003/004/005→006/007→008→009→010→011/012→013→014/015→016/017→018/019→020→021. Hybrid: 021→023→024→025→026→027→028. Publishing: 015/021→029→030→031→032. Bramka 039 wymaga również 022, 031A i 033–038. Ukośnik oznacza gałęzie zależności, nie zgodę na kilka zadań naraz.

Pierwsze pięć: CLOUD-001, 002, 003, 004, 005. Nie zaczynać od Terraform ani podłączenia status-only starego API. M2 jest lokalne, nie drugie środowisko chmurowe. M3 i transporty M5 najpierw mock-only. Scheduler pozostaje paused do końcowej zgody.

## Karty zadań

Wszystkie początkowo **PROPOSED / NOT IMPLEMENTED**; 040 DEFERRED. Rezultat i acceptance oznaczają przyszły stan. Ścieżki Python względem `backend/app/`, testy względem `backend/tests/`, o ile podano inaczej. Wymagania wspólne powyżej obowiązują każdą kartę.

### CLOUD-001 — Przyjęcie kierunku i authority

- **Milestone / zależności:** M0; brak.
- **Cel:** uzyskać jawną decyzję ownera dotyczącą cloud-first/hybrid i granic CLOUD/D###.
- **Istniejące:** AGENTS.md, docs/decisions/0003-desktop-first-architecture.md, docs/INDEX.md, docs/ROADMAP.md, docs/architecture/overview.md.
- **Nowe:** docs/decisions/0004-cloud-first-hybrid-architecture.md po akceptacji.
- **Zakres / kroki:** review CA-01–CA-17, snapshot strategy, primary finishing/fallback i jednego środowiska; zapisać decydenta/datę, zmienić authority/navigation. Bez kodu/infra i bez przepisywania ADR-0003.
- **Testy:** review spójności authority i linków; diff check.
- **Akceptacja:** jawna decyzja ownera z accepted/deferred choices; bez niej pozostawić PROPOSED.
- **Rezultat:** jednoznaczne zasady następnych sesji.
- **Ryzyko / złożoność / rollback:** średnie; S; powrót do PROPOSED bez zmiany desktop backlogu.

### CLOUD-002 — Kontrakty drivera, run i manifest

- **Milestone / zależności:** M0; CLOUD-001.
- **Cel:** ustalić typed ports i immutable wire contracts przed adapterami.
- **Istniejące:** application/automatic_workflow.py, domain/generation_job.py, dependencies.py, storage/artifact_store.py.
- **Nowe:** domain/production_run.py, production_manifest.py, application/production_ports.py, unit/test_production_contracts.py.
- **Zakres / kroki:** driver/observer/policy, state transitions, output slots, fingerprint, run pins, manifest schema/fencing fields; bez SDK i executor rewrite.
- **Testy:** canonical roundtrip, invalid/stare schema, brak secret/absolute paths, rozdzielone narrative/scenes/chunks.
- **Akceptacja:** fixtures jednoznacznie definiują dependencies/hash/timebase/version; kontrakty TARGET_ARCHITECTURE spójne.
- **Rezultat:** implementowalne granice kolejnych zadań.
- **Ryzyko / złożoność / rollback:** średnie; M; nowe DTO obok obecnych, bez zmiany IDs.

### CLOUD-003 — Wydzielenie narracji bez widgetów

- **Milestone / zależności:** M1; CLOUD-002.
- **Cel:** użyć istniejącego durable Speech/recovery bez importów desktop.
- **Istniejące:** desktop/cloud_audio.py, audio_services.py, narration_recovery.py, storage/narration.py.
- **Nowe:** application/narration_service.py, narration_recovery.py, unit/test_headless_narration.py.
- **Zakres / kroki:** selective extraction DTO/generation/recovery; compatibility facades; owner-thread publication i receipts zachować. Bez zmiany modelu, głosu i approval policy.
- **Testy:** import bez Qt/desktop, fake Speech cache/resume/unknown billing, existing narration/desktop audio suites.
- **Akceptacja:** równoważne artefakty/approvals po restarcie, desktop zachowuje zachowanie.
- **Rezultat:** narracja dostępna headless.
- **Ryzyko / złożoność / rollback:** wysokie; L; fasady i stara composition zachowane, artefakty bez zmian.

### CLOUD-004 — Typed driver i observers

- **Milestone / zależności:** M1; CLOUD-002.
- **Cel:** nadać istniejącej orkiestracji neutralny kontrakt.
- **Istniejące:** application/automatic_workflow.py, desktop/pipeline_driver.py, unit/test_automatic_workflow.py.
- **Nowe:** application/pipeline_driver.py, unit/test_pipeline_driver_contract.py.
- **Zakres / kroki:** typed driver, cancel/progress/policy seams, neutralne callbacks zamiast wymagań paneli; desktop adapter nadal obsługuje editor. Bez render/publish w AutomaticWorkflow.
- **Testy:** fake-driver stage flow, blocked/cancel i observers, import graph oraz desktop regression.
- **Akceptacja:** jedna orkiestracja, zgodne dotychczasowe stage decisions.
- **Rezultat:** port pod headless driver.
- **Ryzyko / złożoność / rollback:** średnie; M; additive optional observer i zachowany desktop driver.

### CLOUD-005 — Headless composition scen i projektu

- **Milestone / zależności:** M1; CLOUD-003, CLOUD-004.
- **Cel:** złożyć pełny timeline bez editor object.
- **Istniejące:** desktop/scene_services.py, scene_composition.py, timeline_composition.py, application/projects.py, providers/*_factory.py.
- **Nowe:** application/headless_composition.py, headless_driver.py, integration/test_headless_timeline.py.
- **Zakres / kroki:** selective extraction fasad, prywatna session i injected storage/providers/executables; observer zamiast refresh. Capability policy cloud draft/FHD bez wymuszenia local upscale; bez pozorowania neural SR.
- **Testy:** plan/script/voice/scenes/images/timeline offline, no Qt/GPU import; disabled optional provider nie ładuje runtime.
- **Akceptacja:** selection/revision semantics zachowane, profile cloud/local jawne.
- **Rezultat:** headless timeline.
- **Ryzyko / złożoność / rollback:** wysokie; L; nowy entrypoint opt-in, desktop facades zachowane.

### CLOUD-006 — Budget policy i ledger port

- **Milestone / zależności:** M1; CLOUD-002, CLOUD-003.
- **Cel:** blokować niezatwierdzone lub ponadlimitowe paid requests.
- **Istniejące:** storage/narration.py, providers/openai_llm.py, openai_image.py, workflow/usage.py.
- **Nowe:** application/spending_policy.py, domain/spend_policy.py, unit/test_spend_policy.py.
- **Zakres / kroki:** reserve/commit/unknown dla text/speech/images/repairs/retry, policy revision/caps/expiry; integer micro-USD, injected ledger. Bez paid calls i osłabienia interactive grants.
- **Testy:** pricing expiry fake clock, competing reserves, retry/repair caps; unknown nie zwalnia rezerwy.
- **Akceptacja:** żaden provider dispatch przed grant/reserve; ledger obejmuje wszystkie generatory.
- **Rezultat:** wspólna granica kosztowa.
- **Ryzyko / złożoność / rollback:** wysokie; M; nowa policy tylko headless, desktop approvals zachowane.

### CLOUD-007 — Portable snapshot i handoff manifest

- **Milestone / zależności:** M1; CLOUD-002, CLOUD-005.
- **Cel:** odtworzyć cały projekt bez wspólnego pliku SQLite.
- **Istniejące:** storage/project_migration.py, project_storage.py, local_store.py, result_publication.py.
- **Nowe:** storage/workspace_snapshot.py, application/manifest_builder.py, integration/test_workspace_snapshot.py.
- **Zakres / kroki:** spójny backup wszystkich quiescent DB/index/jobs, relative inventory, canonical manifest/hash; restore do nowego folderu. Bez GCS i real user DB.
- **Testy:** A/B/rejected/approval history po restore, missing/corrupt input, traversal/symlink, schema mismatch.
- **Akceptacja:** snapshot nie jest half-copy; wszystkie dependencies/IDs/bytes weryfikowalne.
- **Rezultat:** przenośny immutable checkpoint.
- **Ryzyko / złożoność / rollback:** wysokie; M; źródło read-only, unknown formats nietknięte.

### CLOUD-008 — Headless rendering FFmpeg

- **Milestone / zależności:** M1; CLOUD-005, CLOUD-007.
- **Cel:** uzyskać playable long preview/final FHD.
- **Istniejące:** application/video_render.py, providers/ffmpeg_render.py, runtime/media_process.py.
- **Nowe:** application/headless_render.py, integration/test_headless_render.py.
- **Zakres / kroki:** renderer/publication z trusted binaries i frozen timeline, oddzielne preview/FHD resize profiles/provenance. Bez neural upscale i Shorts.
- **Testy:** real mały FFmpeg fixture, probe/full decode/duration/cancel/restart reuse.
- **Akceptacja:** 16:9, square pixels, zgodny timing, executable nie pochodzi z request JSON.
- **Rezultat:** zweryfikowany MP4 headless.
- **Ryzyko / złożoność / rollback:** średnie; M; profile opt-in, bez zmiany desktop render version.

### CLOUD-009 — Trzy niezależne Shorts

- **Milestone / zależności:** M1; CLOUD-008.
- **Cel:** reuse long-form bez ponownej płatnej generacji.
- **Istniejące:** application/shorts.py, shorts_director.py, providers/ffmpeg_shorts.py, storage/shorts.py.
- **Nowe:** application/headless_shorts.py, integration/test_headless_shorts.py.
- **Zakres / kroki:** existing repurpose na pinned long/timing, trzy slots/fingerprints/results; jawna selection policy. Bez independent Shorts generation.
- **Testy:** 9:16/rotation/SAR/full decode/caption offsets; fail short-2 i retry bez regeneracji long/short-1/3.
- **Akceptacja:** trzy poprawne portrait outputs albo partial/review, nigdy fałszywy success.
- **Rezultat:** izolowane pochodne Shorts.
- **Ryzyko / złożoność / rollback:** średnie; M; parent media read-only, warianty zachowane.

### CLOUD-010 — QA i lokalny pionowy przekrój

- **Milestone / zależności:** M1; CLOUD-006, CLOUD-009.
- **Cel:** udowodnić pełne offline 1+3 bez desktopu.
- **Istniejące:** application/production_quality.py, quality_sources.py, quality_validators.py, publishing_package.py.
- **Nowe:** application/headless_quality.py, tooling/headless_production.py, integration/test_headless_production.py.
- **Zakres / kroki:** existing Quick/Deep QA i read models, CLI, manifest/package; approved fixture topic. Bez nowego silnika, research i publish.
- **Testy:** małe playable media + 600s timing fixture, restart checkpoints/cache equivalence, corrupted final i stale QA.
- **Akceptacja:** objective QA oddzielnie od editorial review; exact identities/history zachowane, desktop suite bez regresji.
- **Rezultat:** pierwszy lokalny pełny slice.
- **Ryzyko / złożoność / rollback:** wysokie; M; CLI opt-in i niezmieniony desktop.

### CLOUD-011 — Firestore control repository

- **Milestone / zależności:** M2; CLOUD-002, CLOUD-006.
- **Cel:** durable run/operation/lease/budget state.
- **Istniejące:** jobs/coordinator.py i domain/generation_job.py jako odniesienie kontraktów.
- **Nowe:** cloud/repositories.py, integration/test_cloud_repository.py.
- **Zakres / kroki:** unique slot/idempotency, lease/epoch, transactional ledger i paginated read models. Nie podmieniać local JobRepository na SDK i nie zapisywać mediów w Firestore.
- **Testy:** emulator/fake concurrency, jedna claim, stale epoch denied, monthly cap races, bounded retry/pagination/time injection.
- **Akceptacja:** dwie instancje widzą te same committed operations, bez live cloud.
- **Rezultat:** control plane niezależny od HTTP procesu.
- **Ryzyko / złożoność / rollback:** wysokie; M; versioned collections/feature flag, local queue bez zmian.

### CLOUD-012 — GCS streaming adapter

- **Milestone / zależności:** M2; CLOUD-007.
- **Cel:** durable immutable bytes i materialization.
- **Istniejące:** storage/artifact_store.py, manifest.py, local_store.py.
- **Nowe:** cloud/artifacts.py, unit/test_gcs_artifacts.py.
- **Zakres / kroki:** streaming upload/download, create-only/generation, SHA/size/type, pinned reads/grants port; bez filesystem mount.
- **Testy:** fake bucket: same-byte duplicate, different-byte collision, partial upload/corrupt checksum, bounded memory.
- **Akceptacja:** kontrakty StreamingArtifactStore tam, gdzie semantyka zgodna; brak overwrite.
- **Rezultat:** cloud artifact adapter.
- **Ryzyko / złożoność / rollback:** średnie; M; LocalArtifactStore zachowuje zachowanie.

### CLOUD-013 — Checkpointy, fencing i dispatch recovery

- **Milestone / zależności:** M2; CLOUD-010, CLOUD-011, CLOUD-012.
- **Cel:** wznawiać batch bez zgubionych operacji i powtórnego paid dispatch.
- **Istniejące:** application/headless_driver.py, storage/workspace_snapshot.py z wcześniejszych kart.
- **Nowe:** cloud/runner.py, dispatcher.py, integration/test_cloud_checkpoints.py.
- **Zakres / kroki:** snapshot→GCS→CAS head, receipts per call, budget reserve, heartbeat, dispatch_pending i reconcile expired/pending. Bez nowego workflow engine.
- **Testy:** crash w każdym oknie upload/manifest/Firestore commit, late writer, receipt missing/unknown, Jobs.run failure.
- **Akceptacja:** stale owner nie zmienia head; ambiguous paid call blokuje retry; dispatch failure zostawia operation.
- **Rezultat:** resumable durable runner.
- **Ryzyko / złożoność / rollback:** wysokie; L; checkpoint compatibility i kwarantanna uncommitted bytes.

### CLOUD-014 — Durable API v2

- **Milestone / zależności:** M2; CLOUD-013.
- **Cel:** HTTP tworzy trwałe operacje, a nie pozorny running.
- **Istniejące:** api/main.py, dependencies.py, schemas.py, routes/workflow_runs.py (v1 zachowane).
- **Nowe:** api/v2/routes.py, schemas.py, integration/test_production_api.py.
- **Zakres / kroki:** channels/runs/operations/stages/artifacts/commands/approvals; 202, short dispatch, Idempotency-Key/expectedVersion i structured errors. Bez inference w handler.
- **Testy:** ASGI restart, dwa apps ze wspólnym store, payload conflict/stale revision, append-only approval exact hash.
- **Akceptacja:** durable pending widoczne po awarii dispatch i restart; local v1 regression zachowane.
- **Rezultat:** produkcyjna granica HTTP v2.
- **Ryzyko / złożoność / rollback:** średnie; M; osobny router/mode, cloud nie montuje legacy paid writes.

### CLOUD-015 — IAP i role boundaries

- **Milestone / zależności:** M2; CLOUD-014.
- **Cel:** prywatny dashboard/API i scoped worker identity.
- **Istniejące:** api/main.py, dependencies.py.
- **Nowe:** api/auth.py, cloud/settings.py, integration/test_cloud_auth.py.
- **Zakres / kroki:** signed JWT/issuer/audience/expiry, owner/viewer/worker, channel scope, CSRF, preview grants i fail-closed settings; bez provisioning.
- **Testy:** podpisane fixtures, forged headers, foreign channel, anonymous/static/media denied, wrong origin, worker paid/approval denied.
- **Akceptacja:** brak cloud bypass; OAuth callback seam i auth errors nie ujawniają danych.
- **Rezultat:** gotowa aplikacyjna granica security.
- **Ryzyko / złożoność / rollback:** wysokie; M; local test auth oddzielnie od cloud mode.

### CLOUD-016 — Minimalny dashboard i review

- **Milestone / zależności:** M2; CLOUD-014, CLOUD-015.
- **Cel:** ręczny start i przejrzysty review bez desktopu.
- **Istniejące:** api/v2/* z poprzednich kart.
- **Nowe:** web/routes.py, templates/*, static/*, integration/test_web_dashboard.py.
- **Zakres / kroki:** Jinja/HTMX dashboard/channels/settings/queue/run/progress/script/media/Shorts/approvals/costs/errors; bounded polling. Worker/calendar pokazują truthful unavailable do kolejnych kart.
- **Testy:** fake backend/browser: start 202, reload/progress, stale approve, scoped regeneration estimate, podstawowy responsive layout.
- **Akceptacja:** bez CDN i sekretów; supported actions odpowiadają API, bez udawania pełnego editor.
- **Rezultat:** użyteczny manual production UI.
- **Ryzyko / złożoność / rollback:** średnie; M; web obok desktop, additive routes.

### CLOUD-017 — Kontener i batch entrypoint

- **Milestone / zależności:** M3; CLOUD-010, CLOUD-013, CLOUD-015.
- **Cel:** uruchomić ten sam core w minimalnym Linux obrazie.
- **Istniejące:** pyproject.toml, runtime/media_process.py, providers/ffmpeg_render.py.
- **Nowe:** Dockerfile, .dockerignore, cloud/__main__.py, composition.py, dependency lock, scripts/cloud/container_smoke.py.
- **Zakres / kroki:** API/batch entrypoint, PORT, non-root, FFmpeg/fonts, no Qt/GPU/private context; produce/reconcile/publish trusted args.
- **Testy:** Linux import graph, scans, offline 1+3 smoke, SIGTERM/resume, full 10-min timing/render peak RSS/scratch benchmark.
- **Akceptacja:** zmierzone limity profilu i brak provisioning modeli/paid calls.
- **Rezultat:** deployable mock image.
- **Ryzyko / złożoność / rollback:** wysokie; M; immutable digest, desktop packaging bez zmian.

### CLOUD-018 — Terraform jednego środowiska

- **Milestone / zależności:** M3; CLOUD-015, CLOUD-017.
- **Cel:** przygotować reviewable infrastrukturę z parametrami.
- **Istniejące:** pyproject/deployment constraints; brak obecnej cloud composition Studio.
- **Nowe:** infra/gcp/main.tf, variables.tf, outputs.tf, backend example, operator bootstrap docs.
- **Zakres / kroki:** resources/state/IAM/secrets refs/IAP/WIF, protect data, Scheduler paused, jawne ownership resource vs image. Bez apply/import/cloud mutation.
- **Testy:** fmt/validate i offline config/policy; private access, narrow roles, min=0, jeden deployment, brak Tripse IDs.
- **Akceptacja:** operator może review plan, bez obowiązku akceptacji niewidocznych zmian.
- **Rezultat:** parametryzowana definicja infra.
- **Ryzyko / złożoność / rollback:** wysokie; M; state poza Git, brak zmiany istniejących zasobów.

### CLOUD-019 — CI i immutable release tools

- **Milestone / zależności:** M3; CLOUD-017, CLOUD-018.
- **Cel:** jedna ścieżka test/build/deploy/rollback.
- **Istniejące:** istniejące .github/workflows (zachować desktop checks).
- **Nowe:** scripts/cloud/config.py, release.py, smoke.py, rollback.py, .github/workflows/cloud-ci.yml, cloud-delivery.yml.
- **Zakres / kroki:** PR offline bez credentials; protected delivery/WIF/exact SHA+digest/scans/concurrency/freshness/evidence. Provision oddzielnie, bez DEV/PROD.
- **Testy:** fake CLI/registry: stale run, digest/config mismatch denied; rollback accepted proof only; CI bez paid calls/apply.
- **Akceptacja:** reviewable workflow z minimalnymi permissions i pin Actions SHA.
- **Rezultat:** powtarzalny release pipeline.
- **Ryzyko / złożoność / rollback:** wysokie; M; aktywacja workflow po owner review, stary desktop CI zachowany.

### CLOUD-020 — Operator bootstrap i private mock shell

- **Milestone / zależności:** M3; CLOUD-018, CLOUD-019.
- **Cel:** zweryfikować jedno rzeczywiste private środowisko.
- **Istniejące:** infra/gcp/* i scripts/cloud/* z wcześniejszych kart.
- **Nowe:** docs/cloud-migration/acceptance/cloud-shell.md bez secrets.
- **Zakres / kroki:** po osobnej zgodzie na projekt operator review state/plan/IAM i apply; protected mock API/Job deploy, quotas/region/IAP. Bez paid secrets i weekly enable.
- **Testy:** real owner login, anonymous denied, signed headers, health/version i mock operation; zachować digest/config/IAM evidence.
- **Akceptacja:** operator potwierdził provision/deploy; brak zgody blokuje tylko tę bramkę.
- **Rezultat:** private cloud shell.
- **Ryzyko / złożoność / rollback:** wysokie; M; Scheduler paused, known digest rollback, bez destroy danych.

### CLOUD-021 — Ręczny cloud pionowy przekrój

- **Milestone / zależności:** M3; CLOUD-016, CLOUD-020.
- **Cel:** dowieść request→Job→artifacts→review UI.
- **Istniejące:** cloud/runner.py, repositories.py, artifacts.py, web/*.
- **Nowe:** integration/test_cloud_vertical_slice.py, docs/cloud-migration/acceptance/manual-run.md.
- **Zakres / kroki:** mock generation, playable preview/3 portrait/QA, durable checkpoints i GCS; restart execution/API. Bez live paid APIs w tej karcie.
- **Testy:** cloud mock smoke, hashes, checkpoint restart, duplicate manual request i short-only retry; desktop regression.
- **Akceptacja:** outputs odtwarzalne po utracie scratch, bez fałszywego completed.
- **Rezultat:** pierwszy użyteczny cloud slice.
- **Ryzyko / złożoność / rollback:** wysokie; M; wyłączyć dispatcher, zachować manifests i private shell.

### CLOUD-022 — Kanały i Scheduler reconcile

- **Milestone / zależności:** M3; CLOUD-021.
- **Cel:** weekly slots i recovery niezależne od browsera.
- **Istniejące:** domain/channel_library.py, cloud/runner.py, api/v2/*.
- **Nowe:** application/production_schedule.py, unit/test_production_schedule.py, schedule runbook.
- **Zakres / kroki:** versioned channel/schedules, unique UTC slots/timezone, missed-slot grace, bounded hourly reconcile i Run Jobs API OAuth; optional weekly trigger tego samego Job. Scheduler początkowo paused, bez paid enable.
- **Testy:** fake DST spring/fall, duplicate slot, overlap channels, pending dispatch recovery, paused schedule i no catch-up storm.
- **Akceptacja:** real mock tick tylko po zgodzie; jeden tick nie tworzy duplikatów i nie przekracza caps; owner akceptuje hourly recovery SLO i minutowy narzut billing Jobs.
- **Rezultat:** gotowe scheduling 1+3.
- **Ryzyko / złożoność / rollback:** średnie; M; pause bez kasowania runs.

### CLOUD-023 — Uwierzytelnianie workera

- **Milestone / zależności:** M4; CLOUD-015, CLOUD-021.
- **Cel:** scoped tożsamość procesu PC bez owner privileges i SA key.
- **Istniejące:** api/auth.py, cloud/settings.py.
- **Nowe:** local_worker/auth.py, api/v2/worker.py, unit/test_worker_auth.py, auth acceptance guide.
- **Zakres / kroki:** dedykowany principal, IAP programmatic OAuth, secure local refresh store, task claims. Real unattended flow zweryfikować przed M4 acceptance.
- **Testy:** fake expiry/revoke/audience/channel; real reboot+refresh bez browsera z zatwierdzonym principal.
- **Akceptacja:** worker nie wykonuje paid generation/approval; brak poprawnego refresh blokuje acceptance, bez public bypass.
- **Rezultat:** bezpieczna tożsamość workera.
- **Ryzyko / złożoność / rollback:** wysokie; M; revoke principal/grants, credentials poza manifestami.

### CLOUD-024 — Transfer i trwała kolejka lokalna

- **Milestone / zależności:** M4; CLOUD-007, CLOUD-012, CLOUD-023.
- **Cel:** odporne pobieranie i oddawanie pakietów.
- **Istniejące:** storage/paths.py, local_store.py.
- **Nowe:** local_worker/client.py, repository.py, transfer.py, integration/test_worker_transfer.py.
- **Zakres / kroki:** SQLite checkpoints/task lease, selective downloads, partial ranges/generation pin/SHA, staging upload sessions. Bez GPU/render.
- **Testy:** power loss download/upload, zmiana generation, duplicate assignment, disk full, traversal/zip-slip/symlink/size cap z fake HTTP.
- **Akceptacja:** restart odzyskuje poprawny partial, nie traktuje niezweryfikowanych plików jako complete.
- **Rezultat:** idempotentny transport.
- **Ryzyko / złożoność / rollback:** wysokie; M; cleanup tylko own scratch po grace, sources/receipts zachowane.

### CLOUD-025 — Neural image finishing

- **Milestone / zależności:** M4; CLOUD-024.
- **Cel:** reuse Real-ESRGAN na source images.
- **Istniejące:** application/image_upscale.py, providers/local_upscale.py, runtime/upscale_runtime.py, upscale_worker.py.
- **Nowe:** local_worker/image_finishing.py, unit/test_worker_image_finishing.py.
- **Zakres / kroki:** pinned runtime/model/source hash, cache per profile, original immutable. Bez neural video SR i zmiany model defaults.
- **Testy:** fake GPU deterministic receipt, model mismatch, cancel/restart/cache; real GPU benchmark po operator approval, bez paid API.
- **Akceptacja:** nowe master artifacts mają source/model/profile provenance, brak CPU resize udającego SR.
- **Rezultat:** zweryfikowane image masters.
- **Ryzyko / złożoność / rollback:** średnie; M; zachować oryginały i odłączyć opcjonalny SR.

### CLOUD-026 — Local final render i captions

- **Milestone / zależności:** M4; CLOUD-008, CLOUD-009, CLOUD-025.
- **Cel:** final FHD long i portrait Shorts z frozen timing.
- **Istniejące:** providers/ffmpeg_render.py, ffmpeg_shorts.py, application/captions.py, speech_alignment.py, video_finishing.py.
- **Nowe:** local_worker/render_finishing.py, integration/test_worker_final_media.py.
- **Zakres / kroki:** final render z nowych image revisions; opcjonalne dynamic captions z verified alignment. 4K tylko explicit experimental profile, bez default zmiany.
- **Testy:** real FFmpeg geometry/audio/timing/full decode, caption offsets/safe-area, cross-runtime equivalence.
- **Akceptacja:** brak alignment blokuje word-sync; każdy final ma odrębne SHA/profile/QA, preview nie zastępuje final.
- **Rezultat:** local final assets i QA.
- **Ryzyko / złożoność / rollback:** wysokie; M; preview/oryginały zachowane, nowy final manifest.

### CLOUD-027 — Rejestracja final i worker visibility

- **Milestone / zależności:** M4; CLOUD-024, CLOUD-026.
- **Cel:** bezpiecznie ogłosić current final w chmurze.
- **Istniejące:** api/v2/worker.py, cloud/artifacts.py, web/*.
- **Nowe:** application/worker_results.py, integration/test_worker_completion.py.
- **Zakres / kroki:** completion przez lease/epoch/pinned manifest, verify uploaded bytes, parent hash/QA/provenance; UI heartbeat/missing-worker/fallback. Bez YouTube.
- **Testy:** stale completion, tampering, missing output, partial Short failure, offline alert i explicit fallback.
- **Akceptacja:** stary worker nie przesuwa head; brak regeneracji long przy worker retry.
- **Rezultat:** final awaiting_review i status lokalnego wykonawcy.
- **Ryzyko / złożoność / rollback:** wysokie; M; disable claims, stale bytes do kwarantanny, historia zachowana.

### CLOUD-028 — Daemon i Windows autostart

- **Milestone / zależności:** M4; CLOUD-027.
- **Cel:** niezależny proces bez browsera/PySide6.
- **Istniejące:** runtime/resources.py i local_worker/* z wcześniejszych kart.
- **Nowe:** local_worker/__main__.py, scripts/local_worker/task_scheduler.ps1, worker runbook.
- **Zakres / kroki:** polling/backoff, single-instance mutex, GPU lease, graceful shutdown i Task Scheduler start/retry. Skrypt reviewable, aktywacja na PC po wyborze operatora.
- **Testy:** fake clock/process tests; real reboot/network loss/GPU unavailable i recovery na zatwierdzonym PC.
- **Akceptacja:** brak browser/Qt dependency; przy utracie credentials actionable blocked, bez retry storm.
- **Rezultat:** operacyjny worker i revoke/uninstall guide.
- **Ryzyko / złożoność / rollback:** średnie; M; disable scheduled task, zachować SQLite receipts.

### CLOUD-029 — OAuth kanału i token store

- **Milestone / zależności:** M5; CLOUD-015, CLOUD-021.
- **Cel:** durable owner OAuth per channel.
- **Istniejące:** providers/youtube_publishing.py, publishing_factory.py.
- **Nowe:** application/channel_oauth.py, cloud/oauth_store.py, api/v2/oauth.py, integration/test_channel_oauth.py.
- **Zakres / kroki:** code/state/PKCE, owner consent, rzeczywisty channel ID, Secret Manager token versions, refresh/revoke. Bez upload i bez SA jako owner kanału.
- **Testy:** fake replay/state mismatch/expiry/revoke; real account linking tylko za osobną zgodą.
- **Akceptacja:** scopes obejmują potrzebne video/caption/thumbnail methods, token values nie są w Firestore/log/manifest.
- **Rezultat:** bezpieczne konto publikacji.
- **Ryzyko / złożoność / rollback:** wysokie; M; revoke grant, audit refs zachować bez values.

### CLOUD-030 — Resumable private upload i ledger

- **Milestone / zależności:** M5; CLOUD-027, CLOUD-029.
- **Cel:** przesłać final bez RAM copy i niekontrolowanych duplikacji.
- **Istniejące:** providers/youtube_publishing.py, domain/publication.py, application/publishing_package.py.
- **Nowe:** application/upload_service.py, cloud/upload_repository.py, integration/test_resumable_upload.py.
- **Zakres / kroki:** streaming, trwały session/offset/video ID, unique hash/channel key i single upload lease; exact approval/private. Bez thumbnail/schedule.
- **Testy:** fake chunks/restart/ambiguous success→reconcile, no second insert, stale approval denied. Real private upload wyłącznie po konkretnej zgodzie.
- **Akceptacja:** nieznany wynik blokuje nowe insert; checkpoint pozwala dokończyć istniejący upload.
- **Rezultat:** durable private publication record.
- **Ryzyko / złożoność / rollback:** wysokie; L; pause upload, video ID zachować, bez automatycznego delete filmu.

### CLOUD-031 — Metadata, thumbnail, SRT i TikTok export

- **Milestone / zależności:** M5; CLOUD-030.
- **Cel:** kompletny reviewable publishing package.
- **Istniejące:** application/publishing_package.py, providers/local_thumbnail.py, youtube_publishing.py.
- **Nowe:** application/publication_assets.py, integration/test_publication_assets.py.
- **Zakres / kroki:** long/Shorts metadata profiles, synthetic disclosure, thumbnail/caption na existing ID; TikTok export MP4/SRT/thumbnail/metadata. Bez direct TikTok/public publish.
- **Testy:** fake method failure/resume bez video insert, exact hashes/profile/SRT offsets, package regression i export integrity.
- **Akceptacja:** assets retry nie duplikuje video; disclosure/metadata mają revision i review refs.
- **Rezultat:** prywatne video z kompletem assets, TikTok fallback.
- **Ryzyko / złożoność / rollback:** średnie; M; zachować ID/history i retry tylko brakujący asset.

### CLOUD-031A — Lifecycle OpenAI audio

- **Milestone / zależności:** M5, niezależna gałąź możliwa wcześniej; CLOUD-003, CLOUD-006.
- **Cel:** usunąć zależność rollout od wyłączanego Speech modelu.
- **Istniejące:** providers/openai_tts.py, domain/narration.py, tts/*, storage/narration.py.
- **Nowe:** providers/openai_audio_successor.py po wyborze kontraktu, audio migration fixtures/report.
- **Zakres / kroki:** aktualna weryfikacja lifecycle/kontraktu, jawny wybór ownera; oddzielny adapter jeśli Realtime, zachowanie normalized PCM/chunks/cache identity/budżetu. Bez cichego fallback. Jeśli >L, rozbić discovery i adapter przed kodowaniem.
- **Testy:** offline protocol fakes/unknown billing; porównanie głosu/timing/quality/cost wyłącznie po zgodzie na bounded paid sample.
- **Akceptacja:** zaakceptowany aktualny provider, ponowna walidacja audio/QA/estymat przed długim rollout.
- **Rezultat:** trwały kierunek audio, bez automatycznej podmiany model ID.
- **Ryzyko / złożoność / rollback:** wysokie; L; stare audio odtwarzalne, stary provider tylko do końca dostępności.

### CLOUD-032 — Approval-gated scheduling publikacji

- **Milestone / zależności:** M5; CLOUD-022, CLOUD-031.
- **Cel:** review i publishing calendar z durable commands.
- **Istniejące:** domain/publication.py, api/v2/*, web/*.
- **Nowe:** application/publication_schedule.py, integration/test_publication_schedule.py.
- **Zakres / kroki:** approve exact final/QA/metadata; future UTC publishAt/private/never-published; manual-private default, scheduled mode jawnie wybrany. Bez zgody pochodzącej od generatora.
- **Testy:** past date denied, UTC/DST, stale approval, revoked OAuth, status retry bez upload, channel mismatch.
- **Akceptacja:** real schedule wyłącznie po owner approval; nie wysyłać daty przeszłej grożącej natychmiastowym public.
- **Rezultat:** kontrolowany kalendarz i historia decyzji.
- **Ryzyko / złożoność / rollback:** wysokie; M; pause scheduling, private video/history/ID zachowane.

### CLOUD-033 — Topic selection i jakość redakcyjna

- **Milestone / zależności:** M6; CLOUD-010, CLOUD-022.
- **Cel:** powtarzalny, nierepetytywny weekly input.
- **Istniejące:** application/video_planning.py, narrative_continuity.py, planned_script_generation.py, modules/research.py.
- **Nowe:** application/topic_selection.py, unit/test_topic_selection.py.
- **Zakres / kroki:** approved topic queue default, anti-repeat reservations/history i source provenance; optional research port/allowlist/budget. Nie zakładać jakości/factual currency starego ResearchModule.
- **Testy:** offline source fixtures, exhausted queue→review, duplicate reservation, script continuity i paid search disabled.
- **Akceptacja:** owner editorial rubric dla pilota, jawne źródła i brak powtórzeń bez uzasadnienia.
- **Rezultat:** channel-configured topic selection.
- **Ryzyko / złożoność / rollback:** średnie; M; powrót do approved topics, rejected/source history zachowane.

### CLOUD-034 — Kalibracja kosztów i limity

- **Milestone / zależności:** M6; CLOUD-006, CLOUD-013, CLOUD-021, CLOUD-031A.
- **Cel:** zatwierdzone realne caps i widoczność usage.
- **Istniejące:** application/spending_policy.py, cloud/repositories.py, storage/narration.py, web/*.
- **Nowe:** scripts/cloud/cost_report.py, integration/test_cloud_budget_limits.py.
- **Zakres / kroki:** actual model/size pricing, estimates/receipts/billing reconciliation, run/channel/month/retry caps; bez samoczynnego zwiększania limitów.
- **Testy:** offline concurrency/expiry/global rate limiter/repair cap; bounded live sample po osobnej zgodzie, estimate vs receipt vs billing.
- **Akceptacja:** dashboard rozdziela estimated/reserved/known actual/unknown; żaden wyścig nie omija cap.
- **Rezultat:** wiarygodna granica wydatków produkcyjnych.
- **Ryzyko / złożoność / rollback:** wysokie; M; fail closed po expiry/unknown, receipts zachowane.

### CLOUD-035 — Observability i alerty

- **Milestone / zależności:** M6; CLOUD-021, CLOUD-027, CLOUD-030.
- **Cel:** actionable postęp, awarie i koszty.
- **Istniejące:** cloud/runner.py, api/v2/*, local_worker/*.
- **Nowe:** application/production_events.py, scripts/cloud/diagnostics.py, monitoring templates.
- **Zakres / kroki:** structured run/stage/attempt/channel/output events, latency/lease/worker/budget/upload metrics; stuck/failed/unknown alert, redaction/retention. Bez raw prompts/audio/tokens/URLs.
- **Testy:** fake sink/redaction/bounded volume; real log/alert smoke po zgodzie, bez paid generation.
- **Akceptacja:** operator potrafi wskazać checkpoint i bezpieczną następną akcję; dashboard nie ukrywa errors.
- **Rezultat:** operacyjna widoczność.
- **Ryzyko / złożoność / rollback:** średnie; M; wyłączyć sink/alert, historia pozostaje.

### CLOUD-036 — Niedestrukcyjny import desktop projects

- **Milestone / zależności:** M6; CLOUD-007, CLOUD-012, CLOUD-013.
- **Cel:** udostępnić istniejącą historię bez niszczenia lokalnych projektów.
- **Istniejące:** storage/project_migration.py, project_storage.py, application/history.py.
- **Nowe:** tooling/cloud_project_import.py, integration/test_legacy_cloud_import.py.
- **Zakres / kroki:** closed/backup export do nowego cloud project, zachować IDs/revisions/rejected/approvals/media lineage; ancestry conflict/unsupported schema detection. Bez live bidirectional sync.
- **Testy:** multi-revision fixtures, Unicode paths, corrupt/missing bytes, idempotent reimport i restore do nowego folderu; bez real user DB.
- **Akceptacja:** oryginał identyczny, każdy cloud artifact ma source mapping; konflikty jawne.
- **Rezultat:** odwracalna migracja kopii.
- **Ryzyko / złożoność / rollback:** wysokie; M; wrócić do oryginału/zweryfikowanego eksportu.

### CLOUD-037 — Recovery, chaos i restore

- **Milestone / zależności:** M6; CLOUD-022, CLOUD-028, CLOUD-032, CLOUD-034, CLOUD-035, CLOUD-036.
- **Cel:** udowodnić odporność całego hybrid flow.
- **Istniejące:** nowe cloud/worker/publication integration tests.
- **Nowe:** integration/test_hybrid_recovery.py, docs/cloud-migration/acceptance/recovery.md.
- **Zakres / kroki:** restart API/Job/PC, GCS↔Firestore crash windows, stale lease, unknown billing, duplicate Scheduler, revoked OAuth/failed Short; restore do nowej lokalizacji. Bez destrukcji produkcyjnych danych.
- **Testy:** offline fault matrix, isolated real restore drill/controlled process kill po zgodzie.
- **Akceptacja:** history/IDs/receipts zachowane, brak automatycznego paid retry przy unknown i duplikacji upload.
- **Rezultat:** recovery evidence i runbook.
- **Ryzyko / złożoność / rollback:** wysokie; M; stop nowych operacji, ostatni committed snapshot.

### CLOUD-038 — Release i rollback drill

- **Milestone / zależności:** M6; CLOUD-019, CLOUD-021, CLOUD-035.
- **Cel:** dowieść powtarzalnego powrotu do known-good release.
- **Istniejące:** scripts/cloud/release.py, rollback.py, infra/gcp/*.
- **Nowe:** docs/cloud-migration/acceptance/release.md, operator runbooks.
- **Zakres / kroki:** evidence chain API+Job+schema, stop schedule/drain, config/secret rotation, compat window; bez resetu DB/GCS i drugiego środowiska.
- **Testy:** fake proof validation i real mock-only rollback po zgodzie; auth/schema/pending runs po powrocie.
- **Akceptacja:** zgodny digest/config/evidence i operator-reviewed drift; nie destructively downgrade danych.
- **Rezultat:** release/rollback z dowodem.
- **Ryzyko / złożoność / rollback:** wysokie; M; przywrócić accepted digest/config, forward-compatible dane.

### CLOUD-039 — Pilot 1+3 i zgoda na unattended

- **Milestone / zależności:** M6; CLOUD-028, CLOUD-032, CLOUD-033, CLOUD-034, CLOUD-037, CLOUD-038.
- **Cel:** zaakceptować realną osobistą produkcję.
- **Istniejące:** zaimplementowany cloud/hybrid slice i dokumentacja.
- **Nowe:** docs/cloud-migration/acceptance/weekly-pilot.md.
- **Zakres / kroki:** po jawnej zgodzie i cap jeden real long około 600s + 3 portrait, local finish/QA/review/private upload; następnie obserwowany weekly pilot. Paid/schedule enable oddzielnie potwierdzone przez ownera; bez deklaracji monetization readiness.
- **Testy:** objective geometry/timing/loudness/decode, subjective continuity/visual/captions, billing/unknown i no duplicates; komplet IAP/worker/OAuth/restore/release proofs.
- **Akceptacja:** owner zatwierdza profil, budget i unattended scope; brak twardych blockerów, residual risks zapisane.
- **Rezultat:** operacyjnie zaakceptowane 1+3 tygodniowo.
- **Ryzyko / złożoność / rollback:** wysokie; M; pause Scheduler/paid dispatch, zachować finals/history/private videos i desktop fallback.

### CLOUD-040 — Opcjonalny Drive mirror

- **Milestone / status / zależności:** poza ścieżką, DEFERRED; CLOUD-027, CLOUD-039.
- **Cel:** wygodny alternatywny delivery/export.
- **Istniejące:** cloud/artifacts.py, local_worker/transfer.py.
- **Nowe:** providers/drive_delivery.py, unit/test_drive_delivery.py.
- **Zakres / kroki:** mirror GCS package z immutable completion marker i API-pinned hash, osobny OAuth/quota. Drive nie jest queue/source of truth. Bez zmiany MVP protokołu.
- **Testy:** fake partial/stale/sync duplicate, secret-free manifest, primary source pozostaje GCS; live sync tylko za zgodą.
- **Akceptacja:** worker nie ufa samemu plikowi Drive; integrity zgodna z API assignment.
- **Rezultat:** opcjonalny transport bez blokady core.
- **Ryzyko / złożoność / rollback:** średnie; M; disable mirror, GCS worker działa dalej.

## Rollout i granice dowodów

| Faza | Offline merge gate | Human/cloud acceptance |
| --- | --- | --- |
| Baseline/M0 | Existing suite i czyste rozgraniczenie zmian | Failures sklasyfikowane w README; brak live claims |
| M1 | Fakes/import isolation/desktop regression/real małe FFmpeg fixtures | Full 10-min benchmark bez paid generators |
| M2 | Firestore emulator, fake GCS/Run/OAuth, ASGI restart/auth/idempotency | Bez real cloud potrzebnego do foundation |
| M3 | Container/config/release/dispatch faults | Operator provision/IAP i manual mock job, bez paid defaults |
| M4 | Transfer/lease/power-loss fakes i local FFmpeg | GPU/model, unattended reboot/refresh/revoke i łącze PC |
| M5 | Upload/session/ambiguous/metadata/schedule fakes | Właściwy kanał/private upload/assets/owner review; TikTok eligibility niezakładana |
| M6 | Recovery matrix, budget races, compatibility i full regression | Real 1+3, billing, restore/rollback, explicit weekly/publication approval |

Paid acceptance wymaga wdrożonego ledger/checkpoints oraz aktualnego lifecycle/pricing, a unattended także CLOUD-039. Jedno środowisko może wykonywać mock smoke z oddzielnym namespace/prefix bez generation secrets; to nie drugi deployment. Working project DB/private media nie są fixtures.

## Złożoność całości i blockery

41 kart, w tym jedna decision-only, jedna warunkowa migracja audio i jeden odroczony Drive. S=1, L=5, M=35. Przy podanych widełkach suma nominalna około 51–131 dni skupionej pracy, przed review/owner waits; nie obietnica terminu. Największa niepewność: snapshot consistency, extraction, audio replacement i worker auth. W razie większego zakresu podział karty przed kodowaniem.

Blockery zależnych bramek: brak owner decision, brak worker refresh, niespójny snapshot/fencing/budget, ambiguous billing/upload, failed QA, nieaktualny model/cennik. Baseline pełnego suite ma jeden odtwarzalny failure w integracji D078/duration repair (dokładny wynik w README); wyjaśnić go przed M1 acceptance, przez osobno wybrane zadanie lub jawnie uzasadniony zakres CLOUD-003. Nie uprawnia to do unrelated napraw podczas audytu ani blanket approve. Dowody zapisywać z SHA/digest/config/version, nie samym checkboxem.

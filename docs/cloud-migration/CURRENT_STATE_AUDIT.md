# Audyt stanu obecnego

Data: 2026-10-28. **Zweryfikowane** = odczyt implementacji/testów; nie oznacza akceptacji produkcyjnej. **Częściowe** = istnieją elementy bez kompletnego przepływu. **Proponowane** = przyszła praca. **Niezweryfikowane** = realne konta, GCP, GPU, instalator i płatni dostawcy.

## Zakres i punkt odniesienia

Studio: lokalny working tree `D:/Projects/ai-content-generation`, HEAD `1166dcdfe83ade9c897bc6570293b63704f43545`, origin `https://github.com/piotrbarabasz/ai-content-generation.git`. Tripse: `D:/Projects/tripse-ai-demo`, HEAD `033e63914a3fec8e62daf4af33164d34fb02b29c`, origin `https://github.com/piotrbarabasz/tripse-ai-demo.git`. Oba drzewa na początku czyste. Nie weryfikowano zgodności z najnowszym remote ani stanu wdrożeń Tripse. Pod koniec audytu w Tripse pojawiły się równoległe zmiany working tree w document_studio/frontend/API i testach. Nie pochodzą z tej sesji, nie były edytowane ani cofane; referencyjne ustalenia dotyczą odczytanych wcześniej plików cloud/deployment, nie tych nowych zmian.

Źródła: AGENTS, pyproject, docs/architecture (overview, domain-model, provider-contracts, module-contracts, workflow-engine, api-and-storage), desktop implementation plan, ADR-0003 i wymieniony kod. D### opisuje intencję; zachowanie weryfikuje kod. Desktop-first nadal obowiązuje. Prośba upoważnia do planowania, nie implementacji.

## Zweryfikowane blokady i wejścia produkcyjne

- `backend/app/api/routes/projects.py` utrzymuje PROJECTS, WORKFLOW_CONFIGS, WORKFLOW_RUNS, ARTIFACTS_BY_RUN i EXPORT_BUNDLES w pamięci. `workflow_runs.create_workflow_run` ustawia running/started, a resume zmienia status po kontroli approvals. Nie wywołują silnika. `_ensure_export_bundle` tworzy metadane/referencję manifestu, nie pakiet plików. `api/dependencies.py:ApiDependencies` wstrzykuje głównie TTS preview, nie production dispatcher. Restart i kilka instancji gubią/rozdzielają stan.
- `application/automatic_workflow.py:AutomaticWorkflow.run` nie importuje Qt. Obsługuje plan/script, narration i bounded duration repair, sceny/timing, prompty, obrazy/motion masters i timeline. Kończy na AutomaticWorkflowResult, nie na MP4/Shorts/QA/upload. Duck-typed `desktop/pipeline_driver.py:DesktopPipelineDriver` korzysta z editor.session, paneli audio/video_plan/visuals/timeline, refresh i _bind_regeneration. Brak importu Qt w driverze nie czyni go niezależnym od GUI.
- `desktop/product_composition.py:compose_installed_audio` tworzy `desktop/cloud_audio.py:CloudAudioServices`, importujący desktop.audio_services i desktop.narration_recovery. Otwiera JobRepository(session.repository), ResultArtifactIndex i LocalArtifactStore. „Cloud” oznacza dostawcę Speech, nie Cloud Run. Wybrane klasy bez Qt nadal zależą od lokalnej sesji i desktopowej kompozycji.
- `workflow/engine.py:CoreWorkflowEngine` wykonuje moduły/registry, retries i previous results/checkpoints w procesie; nie jest durable scheduler. `workflow/usage.py` ma domyślny NoopCostTracker. `workflow/presets.py:short_video` pomija wymagane przez sceny scriptGeneration. Nie wybierać go jako gotowego przepływu 1+3. Zachować istniejące silniki; runner cloud ma zapisywać/claimować, a nie tworzyć trzeci silnik.
- `storage/project_repository.py:_connect` wymusza SQLite EXCLUSIVE/DELETE/FULL. ProjectSession ma port/expected revision, ale dalsze usługi czytają session.repository.workspace i konkretne repozytoria. `jobs/repository.py` wiąże jobs.sqlite z sesją, używa BEGIN IMMEDIATE, claim tokenów i attempt numbers. Nowa sesja odzyskuje running jako interrupted. To nie rozproszona lease. `storage/result_publication.py` wraz z jobs używa attached SQLite transaction; tej atomowości nie przenosi podmiana store na GCS.
- `storage/local_store.py` daje immutable publikację, staging/recovery i streaming z SQLite index lub sidecarami; `storage/artifact_store.py` ma ArtifactStore/StreamingArtifactStore. Paths, rename/link/fsync i snapshoty wymagają osobnego protokołu cloud. GCS nie jest filesystemem ani wspólnym SQLite.
- `application/video_render.py:VideoRenderService` przyjmuje renderer, ale publication/coordinator/artifacts prowadzą do local jobs/staging. `providers/ffmpeg_render.py:FFmpegRenderer` bierze trusted executable paths i sprawdza encode/probe/decode. `runtime/media_process.py` ma asyncio subprocess i odnogę Windows. Linux wymaga testu kontenera.

## Macierz ponownego użycia

Ścieżki względem backend/app. Klasyfikacja dotyczy wskazanego zastosowania, nie pełnej akceptacji podsystemu.

| Komponent i dowód | Stan / klasyfikacja | Konieczna praca |
| --- | --- | --- |
| domain/narrative_segment.py, render_scene.py, script.py, dependencies.py, timeline.py | Zweryfikowane / Reuse unchanged | Zachować IDs, lineage, rational timing i rozdzielenie NarrativeSegment/RenderScene/TTS chunks |
| domain/production_quality.py zawiera też local Path context | Częściowo przenośne / Reuse with refactoring | Oddzielić wire/media context, nie uznawać całego domain za cloud DTO |
| application/projects.py, section_editing.py, section_ordering.py | Zweryfikowane porty / Reuse unchanged w prywatnym workspace | Adapter snapshot/import, bez masowego rewrite modeli |
| application/automatic_workflow.py i desktop/pipeline_driver.py | Częściowe headless / Reuse with refactoring | Typed driver, observer/policy; zachować desktop adapter |
| desktop/cloud_audio.py, audio_services.py, narration_recovery.py | Trwałe local audio / Reuse with refactoring | Selective extraction do application i compatibility facades |
| desktop/scene_services.py, scene_composition.py, timeline_composition.py | Fasady i local repos / Reuse with refactoring | Oddzielić refresh i UserDataPaths od bezwidgetowej logiki |
| application/planned_script_generation.py, video_planning.py, narrative_continuity.py, duration_repair.py | Plan/revisions/reviews/repair / Reuse with refactoring | Composition/policy i nowy topic source dla unattended |
| workflow/* i modules/* | Moduły/mock workflows / Reuse unchanged kontraktów; Deprecate later stare presety | Nie przepisywać obecnych klientów w migracji |
| providers/interfaces.py, registry.py, llm_factory.py, image_factory.py, tts_factory.py | Protocols/lazy factories / Reuse unchanged | Cloud/local factories, disabled GPU bez importu modeli |
| providers/openai_llm.py, llm_settings.py | Responses/structured output / Reuse with refactoring | Budget/rate/receipts injection; jawny model, bez fallback |
| providers/openai_image.py | Dokładnie gpt-image-2, quality i usage / Reuse with refactoring | Ledger/checkpoint; nie używać cen GPT Image 1 |
| providers/openai_tts.py, tts/chunk_synthesis.py, assembly.py, storage/narration.py | Speech/cache/PCM/recovery / Reuse with refactoring | Global ledger, composition i osobna ocena lifecycle modelu |
| application/speech_alignment.py, captions.py, providers/whisperx_alignment.py | Alignment i SRT / Reuse with refactoring | Local readers/runtime; Speech nie gwarantuje word timestamps |
| application/video_render.py, video_finishing.py, delivery_export.py, providers/ffmpeg_* | Render/motion/branding / Reuse with refactoring | Snapshot/materialization i testy Linux/fonts/timeouts |
| application/shorts.py, shorts_director.py, providers/ffmpeg_shorts.py, ffmpeg_director.py | Repurpose/portrait / Reuse with refactoring | ShortsService tworzy ProjectShorts i media snapshots; checkpoint per Short |
| application/production_quality.py, quality_validators.py, quality_sources.py, providers/ffmpeg_quality.py | Quick/Deep i separate review / Reuse with refactoring | Materializacja; QA nie potwierdza jakości editorial ani monetyzacji |
| application/publishing_package.py, storage/publishing_package.py, providers/local_thumbnail.py | Metadata/SRT/thumbnail/ZIP / Reuse with refactoring | Local source selection/timing; package to nie upload |
| providers/youtube_publishing.py, publishing_factory.py, modules/publishing.py | Approval gate i transport / Reuse with refactoring | Streaming/OAuth/session persistence/thumbnail/schedule |
| storage/channel_library.py, application/channel_library.py, domain/channel_library.py | Branding poza workspace / Requires new adapter cloud | Biblioteka branding to nie pełny kanał/schedule |
| storage/project_repository.py, jobs/repository.py, result_publication.py | Durable single-owner / Reuse unchanged prywatnie | Requires new adapter cloud control/snapshot; shared SQLite wykluczone |
| storage/local_store.py, artifact_index.py, manifest.py, paths.py | Integrity/recovery/containment / Reuse unchanged local | Requires new adapter GCS streaming/CAS/commit |
| api/routes/* global dictionaries/status-only writes | Częściowe / Replace cloud write path | Nowy v2; zachować v1 lokalnie do jawnej deprecjacji |
| runtime/upscale_worker.py, providers/local_upscale.py, application/image_upscale.py | CUDA Real-ESRGAN SRVGG x4 images; CPU fallback disabled / Keep desktop-only GPU | Worker composition; brak neural video SR |
| runtime/windows_job.py, provisioning, *_windows_x64.json, desktop/deployment.py | Windows runtimes/installer / Keep desktop-only | Osobne Linux composition |
| Widgety desktop/* | Qt editor/playback / Keep desktop-only | Web UI nowy, edytora nie usuwać |
| Drive, worker auth, Firestore/GCS orchestration, GCP CI | Brak kompletnej implementacji / Requires new adapter | Real cloud acceptance Undetermined; Drive DEFERRED |

## Publishing i spending — ograniczenia

GoogleYouTubeTransport._get_service czyta authorized-user OAuth z YOUTUBE_CREDENTIALS_FILE, lazy importuje Google SDK i żąda youtube.force-ssl także dla captions. SA GCP nie daje osobistego kanału. MediaIoBaseUpload używa BytesIO/resumable=True, ale execute nie zapisuje trwałej sesji/offsetu. Provider czyta całe video, więc RAM i ambiguous success są ryzykiem. Domyślne privacy metadata to unlisted; cloud musi jawnie wymusić private. Jest containsSyntheticMedia, brak kompletnego thumbnail/schedule/token-store flow. HTTP publishing to handoff.

storage/narration.py zachowuje append-only settings/estimates/approvals/reservations i exceptional retry. Pricing ma as_of 2026-10-16 i expiry 2026-11-16. Driver pyta panel authorize_narration; automatic może sam akceptować sceny. Scheduled potrzebuje uprzednio zatwierdzonej ograniczonej polityki i jawnego zakresu autoaccept. Brak operatora nie oznacza blanket approve. Primary model gpt-4o-mini-tts ma ogłoszony shutdown; providers/openai_tts.py już zawiera SHUTDOWN=2027-01-06 i check_configuration odmawia po tej dacie. CLOUD-031A to migracja istniejącego jawnego ograniczenia, bez zmiany defaults w audycie; źródła w decyzjach i kosztach.

## Tripse — konkretna mapa referencyjna

| Odczytane źródło Tripse | Adaptować | Nie przenosić |
| --- | --- | --- |
| app/main.py:create_app, app/cloud_runtime.py:build_components | Opt-in/lazy composition, FastAPI/static mount, injection | Travel domain, hardcoded resource regex/names, readonly demo |
| app/cloud_jobs.py:FirestoreJobStore/CloudJobs | Transactions/claim/lease, bounded attempts/recovery | Krótkiej lease demo jako timeout renderu, Tasks bez potrzeby |
| app/document_studio/cloud_storage.py | if_generation_match, immutable bytes/CAS | JSON overlay jako pełne project repo, filesystem emulation |
| Dockerfile | Slim Python, mały context, jawny entrypoint | DATABASE_PATH=/data/tripse.sqlite3, stałego portu bez konfiguracji, braku FFmpeg |
| infra/gcp/cloud-demo/main.tf | Private bucket, prevent_destroy, Firestore, ograniczone IAM/IAP | State/resource IDs/bindings, drugiego DEV/PROD deployment |
| infra/gcp/github-actions/main.tf, builder-isolation.tf | Osobne builder/deployer/WIF trust | Tripse claims i kont |
| .github/workflows/ci.yml, delivery.yml | PR bez credentials, pinned Actions, protected environment, concurrency | DEV→PROD promotion |
| scripts/gcp/deployment_contract.py, deployment.py, ci_config.py, smoke_auth.py, rollback.ps1 | Fail-closed config, exact SHA/digest, evidence/auth smoke/rollback | Naming i gotowych mutation commands |
| docs/DEPLOY_GCP.md, docs/GITHUB_CICD.md | Bootstrap operatora, provision osobno od deploy | Historycznych rollout jako dowodu dla Studio |

Tripse opisuje dowody wdrożeń, ale nie sprawdzano ich w GCP. Nie uruchamiano jego skryptów ani testów i nie zmieniano repo.

## Testy, luki i zachowanie historii

253 pliki pod backend/tests obejmują fixtures/helpers, nie są liczbą testów. Istotne dowody: unit/test_automatic_workflow.py, test_openai_image.py, test_openai_llm.py; integration/test_durable_jobs.py, test_openai_narration.py, test_video_render.py, test_shorts_geometry.py, test_shorts_media.py, test_shorts_director.py, test_production_quality.py, test_publishing_package.py, test_publishing_handoff.py. Geometria ma real FFmpeg/SAR/rotation/decode; QA pełny CPU decode, bounded evidence i odmowę fałszywej approval. Mocks tego nie zastępują.

ShortsDirectorService (application/shorts_director.py) ma child SQLite scope i opcjonalnych dostawców nowej narracji/obrazów. Nie należy utożsamiać jego pełnego trybu reżyserii z bezkosztowym wycięciem z long. MVP korzysta z repurpose w ShortsService, a paid director rewrites pozostają jawnie wyłączone.

Nowe luki: import bez Qt/GPU, headless 1+3, Firestore concurrency/fencing, GCS commit recovery, budget races, restart kontenera i Windows podczas transferu, IAP owner/unattended worker, OAuth expiry/revoke, ambiguous upload. W application/jobs/runtime nie znaleziono bezpośrednich importów PySide/app.desktop; pośrednia kompozycja nadal ma opisane zależności. Wynik lokalnego uruchomienia testów: sekcja weryfikacji README.

Stare projekty pozostają bez zmian. Eksport po zamknięciu edytora albo spójny backup przez storage/project_migration.py i project_storage.py. Nie kopiować żywego SQLite. Import do nowego folderu/cloud project z legacy_project_id, oryginalnymi revision/artifact IDs, checksumami, rejected/approval history i source provenance. Bez dwóch aktywnych writerów: desktop edytuje odłączoną kopię, cloud commands tworzą rewizje. Powrót przez oryginał/zweryfikowany eksport, bez nadpisującego downgrade.

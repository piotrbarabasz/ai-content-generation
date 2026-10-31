# Wdrożenie jednego środowiska i model kosztów

Status: **PROPOSED**, 2026-10-28. Niczego nie provisionowano ani nie wdrażano. Stawki odczytano tego dnia; przykład USD netto bez VAT/przeliczenia PLN, rabatów i gwarancji konta. Ustawienia są parametrami, nie wartościami Tripse.

## Minimalna topologia i konfiguracja

Local development/test + jedno chronione środowisko GCP. Jeden Cloud Run Service (API/web), jeden Cloud Run Job z trybami produce/reconcile/publish, Scheduler, regional GCS, Firestore Native, Artifact Registry, Secret Manager, Logging/Monitoring i WIF dla GitHub Actions. Bez Cloud Tasks, load balancera, VPC connector, VM/GPU cloud, drugiego deploymentu i promocji DEV→PROD. API min instances=0, początkowo max=2; Job tasks=1/parallelism=1, a globalny limit paid work w Firestore, bo limit task nie zabrania równoległych executions.

Proponowany region europe-central2: [Cloud Run locations](https://docs.cloud.google.com/run/docs/locations) oraz [Firestore locations](https://docs.cloud.google.com/firestore/native/docs/locations) potwierdzają Warszawę. GCS regional Warsaw jest na [liście cen](https://cloud.google.com/storage/pricing). Przed provisioning zweryfikować Scheduler/Registry/Secret Manager regional settings, quotas i organization policy w wybranym projekcie. Lokalizacja Firestore nie jest później edytowalna. Brak project ID nie jest powodem wpisywania wartości Tripse.

Parametry wymagane: project_id/project_number, region, service/job/registry/bucket/database names, Terraform backend, owner subjects, worker subject, IAP resource audience i OAuth client, immutable image digest, secret version refs, max instances/task timeout/CPU/RAM/scratch limits, feature mode mock/paid, provider models i pricing expiry, channel configs, schedule disabled/enabled. Fail closed dla braków, mismatched project/bucket/database, emulator endpoint w cloud, public access, latest tag lub nieobsługiwanej manifest schema. Secret values poza konfiguracją/manifest/log. Cloud mode nigdy nie wraca automatycznie do local/demo.

Runtime start: Python 3.11+ (propozycja 3.12 po kompatybilności), slim non-root image, pinned FFmpeg/ffprobe i font set z licencjami, jawne dependencies cloud/web. API słucha 0.0.0.0 na PORT; Job kończy się exit code, nie otwiera serwera. .dockerignore/context allowlist wyklucza projects, media, model caches, .env, SQLite, .git i prywatne audio. Nie instalować PySide6/CUDA/torch w cloud image. Test importu nie może ładować GPU/provisionerów Windows.

## Compute, scratch i timeout

[Cloud Run Jobs timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout) podaje domyślne 10 minut i limit CPU task 168 godzin; GPU task ma inny limit. To maksimum platformy, nie propozycja czasu renderu. Początkowa konfiguracja 2 vCPU/8 GiB i task timeout 2 h wymaga benchmarku pełnego filmu. Limit retries platformy początkowo 0; bounded retries/recovery prowadzi durable runner, aby platforma nie mnożyła paid requests.

[Container contract](https://docs.cloud.google.com/run/docs/container-contract) opisuje, że zapisy do domyślnego filesystemu zużywają pamięć. Nie zakładać „wolnego dysku” dla video. Budżet: RSS Python+FFmpeg+decode buffers+staged images/WAV+MP4 intermediate+final+snapshot+safety. 10 min przy 8 Mb/s to około 600 MB video przed dodatkowymi kopiami/audio. Scratch limit np. 4 GiB w 8 GiB instancji jest hipotezą do pomiaru. Stage i purge seriami po verified upload; nie przechowywać wszystkich encoded wariantów i ZIP równocześnie. Nie montować GCS pod SQLite. Jeśli profil nie mieści się w limitach, local final i cloud low-bitrate preview; dopiero pomiar uzasadnia osobną usługę compute lub ephemeral disk feature.

FFmpeg: bounded stderr/progress, deadline per command, cancel/termination, complete decode dla finals, fixed pixel format/timebase/fonts, threads ≤ przyznanych CPU. Cloud nie ma oczekiwanego NVENC ani modeli lokalnych. Długość audio i crop muszą odpowiadać frozen timeline; fallback CPU nie może po cichu zmieniać efektów. Cloud preview i local final mają oddzielne profile/checksum/QA.

## IAM i tożsamości

| Tożsamość | Minimalna odpowiedzialność | Bez uprawnień |
| --- | --- | --- |
| Operator provisioning | Operator-reviewed Terraform/IAM/bootstrap/state | CI aplikacji nie dziedziczy roli operatora |
| GitHub WIF deployer | Update wskazanego service/job, odczyt digest/evidence, actAs tylko przypisane runtimes | IAM admin, owner/editor projektu, odczyt tokenów YouTube |
| Builder | Push do wskazanego Artifact Registry, bez runtime secrets | Deploy/IAM/project data |
| API runtime | Firestore app state, scoped artifact grants/read, invoke konkretnego Job z allowlisted override | Zmiana job image/SA, nieograniczone secret access |
| Job runtime | GCS stage artifacts, Firestore claim/budget, konkretne generation/publishing secret versions | Provision/deploy/IAM admin |
| Scheduler | Wywołanie wskazanego Run Jobs API | Sekrety, artefakty, dowolne job overrides |
| IAP service agent | run.invoker tylko na wskazanym API service | App data i generation secrets |
| Worker principal | IAP access + application worker role na przypisanych kanałach | Bucket-wide IAM, approval/paid generation i YouTube OAuth |

Firestore server SDK używa IAM; browser nie ma bezpośredniego dostępu. App scope owner/channel jest obowiązkowy także przy szerokim technicznie SDK role. IAM nie daje natywnego podziału kolekcji per aplikacyjny kanał; to ograniczenie do uwzględnienia przy przyszłym multi-tenant. Preferować osobne runtime SA, custom/narrow roles i ograniczone resource bindings; sprawdzić efektywne permissions. API nie musi mieć OpenAI ani YouTube secret access. Signed grants wymagają zatwierdzonego mechanizmu podpisu (IAM signBlob na dedykowanym signer); nie pobierać prywatnego klucza SA.

Direct [IAP on Cloud Run](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run) bez public bypass, signed JWT validation i role mapping. Bootstrap external/no-organization OAuth może wymagać Console. [Programmatic IAP auth](https://docs.cloud.google.com/iap/docs/authentication-howto): osobno zaplanować custom client/allowlist i worker refresh. IAP OAuth audience klienta i resource audience signed header to różne wartości. Scheduler wywołujący googleapis.com Run API używa OAuth access token. Nie kopiować Tripse Tasks OIDC audience do tego przepływu.

YouTube owner OAuth niezależny od GCP ADC. Refresh tokens w Secret Manager, refs/revision/revocation w Firestore, rotation/reauthorize testowane; authorization callback state/PKCE i przypisanie do rzeczywistego channel ID. Consent/test-mode/token expiry/quota są bramką operatora, nie kodowym mockiem.

## Provisioning, delivery i rollback

Terraform: jeden backend ze zdalnym chronionym state, versioning/access ograniczonym do operatora, bez state/tfvars/plan/secrets w Git. Plany mogą zawierać dane wrażliwe: private evidence. Lifecycle prevent_destroy dla trwałych danych, backup/PITR i soft-delete parametryzowane. Bez cichego import/adopt istniejących zasobów. Application deployment nie wykonuje terraform apply i nie poprawia automatycznie IAM. Granice ownership: Terraform resource settings/IAM, delivery image digest/revision dla service/job; wykluczyć wzajemne nadpisywanie przy kolejnym plan.

GitHub Actions: jedna chroniona deployment environment (nazwa np. cloud), wybrana przez właściciela approved branch, local dev poza GCP. PR checks bez cloud credentials i bez pull_request_target dla niezaufanego kodu. Delivery wymaga udanych testów i environment approval, otrzymuje id-token:write wyłącznie w deploy job; WIF trust ograniczony immutable repository identity/ref/environment. Actions przypięte SHA. Jeden concurrency mutex dla deployment/acceptance/rollback, cancel-in-progress=false i freshness watermark, aby stary run nie nadpisał nowego.

Przepływ:

1. PR: Python offline tests, Windows desktop regression oraz Linux headless/container smoke, dependency/license/security scan, secret scan, config/schema tests, Terraform fmt/validate bez apply.
2. Z approved SHA: deterministyczny allowlisted context, dependency lock, SBOM/scan i build obrazu; registry digest pin, bez latest i rebuild podczas deploy.
3. Preflight: project/region/identities/config, expected auth mode, secret refs i quotas; bez live paid request.
4. Deployment API revision + Job digest jako kompatybilna release para; drain aktywne runs lub przypiąć ich kontynuacje do kompatybilnego engine/manifest version. Scheduler nadal paused.
5. Authenticated smoke: anonymous denied, owner read, worker scoped, mock operation→artifact, restart/retry, image SHA/digest/schema. Nie udawać pełnej produkcji przez sam health 200.
6. Immutable release evidence: source SHA/context hash, image digest, dependency/SBOM/scan refs, API revision, Job digest/config version, schema compat, test/smoke results, Actions run/attempt/artifact ID. Żadnych credential values.
7. Rollback: zweryfikowany wcześniejszy digest/evidence, traffic API na znaną revision i Job na zgodny digest; wstrzymać Scheduler/new paid work, sprawdzić aktywne attempts i lease. Nie cofać destrukcyjnie DB/GCS. Stary binary musi umieć czytać manifesty albo rollout zostaje zablokowany. Forward-only data migration z backup/compatibility window.

Operator acceptance: ręczny Google login/IAP, worker unattended token refresh/revocation, restore danych do nowej lokalizacji, prawdziwy manual paid run z limitem, final QA i private upload na właściwy kanał. Dopiero po tych dowodach owner może włączyć weekly i dalsze scheduling. Audyt niczego nie aktywuje.

## Model kosztów — jawne jednostki

Bazowo 4 runy/miesiąc: 4×10 min long + 12 Shorts wyciętych z long. Shorts nie wymagają drugiej narracji/generacji obrazów. Kolejny kanał mnoży przede wszystkim koszt zmienny, nie liczbę deploymentów. Miesiąc kalendarzowy średnio ma 4.35 tygodnia; wariant 4 jest wymaganym baseline, roczny forecast pomnożyć przez 4.35/4.

[OpenAI pricing](https://developers.openai.com/api/docs/pricing), odczyt 2026-10-28: przykład text gpt-6.1-terra standard input $1/M, output $5/M; gpt-4o-mini-tts text $0.60/M, audio output $12/M; gpt-image-2 text $5/M, image output $30/M. Są to przykłady cennika, nie zmiana konfiguracji ani gwarancja dostępności konta.

[Image cost calculator/table](https://developers.openai.com/api/docs/guides/image-generation) podaje dla gpt-image-2 1536×1024 output około $0.005 low, $0.041 medium, $0.165 high; input dolicza się osobno. Rozmiar jest przykładem kalkulacyjnym, nie final 16:9; inne rozmiary/quality zmieniają usage. Nie przenosić token counts starszych GPT Image modeli. Limit kosztu wymaga wersjonowanej estymaty dla faktycznego request.

Formuły:

- Text: `(Tin × Pin + Tout × Pout + cachedTin × Pcached)/1e6`; wszystkie script/planning/repair/research/metadata requests sumować. Nie zakładać cache rabatu bez dowodu.
- Speech: `(text_tokens × 0.60 + audio_output_tokens × 12)/1e6`. Minuty nie określają dokładnie audio tokens; poniżej przyjęto 12k/15k/50k output tokens dla dziesięciominutowej narracji i retry. To założenia do pomiaru, nie stała cena za minutę.
- Images: `N × multiplier_attempts × (output_cost(size,quality) + prompt_tokens × 5/1e6 + reference_image_cost)`. Reuse Shorts nie mnoży N przez 4.
- Run compute: `max(60, execution_seconds) × (vCPU × pCPU + GiB × pRAM)` dla każdej task instance; doliczyć wszystkie reconcile/publish/retry executions. API ma osobny request-based billing.
- Storage: `average_retained_GiB × pGBmonth + classA × pA + classB × pB + soft-delete/version/backup_GiB × pGBmonth`.
- Firestore: reads/index reads + writes + deletes + storage + backup/PITR; odpytywanie UI ma znaczenie.
- Transfer: `internet_GiB × pEgress + cross_region_GiB × pCross`; local download i browser previews mogą kosztować, upload ingress nie znosi kosztu pobrania z GCS.

[Cloud Run pricing](https://cloud.google.com/run/pricing) pokazuje rozliczenie CPU/GiB-s i Jobs przez active execution; konkretne SKU zależy od regionu/billing mode. Do ostrożnego modelu przyjęto **założone sufity kalkulacyjne**, nie ofertę Warsaw: pCPU=$0.000030/vCPU-s, pRAM=$0.000004/GiB-s. Przed provisioning zastąpić odczytem regionalnego kalkulatora/SKU. Nie odliczamy free tier/CUD. Sufity są wyższe od części tabel bazowych, lecz nie stanowią gwarantowanego maksymalnego rachunku.

GCS Standard w modelu: założenie $0.02–0.04/GiB-month; transfer internet $0.12–0.20/GiB jako rezerwa planistyczna, nie zweryfikowana stawka Warsaw. Aktualne regionalne SKU z [Storage pricing](https://cloud.google.com/storage/pricing) należy przypiąć przed zatwierdzeniem budżetu. Run/GCS w jednym regionie ogranicza transfer cross-region. Named Firestore nie korzysta z free quota; [Firestore pricing](https://cloud.google.com/firestore/pricing). W małej instalacji rozważyć wolny default DB w dedykowanym projekcie, bez zakładania dostępnego darmowego limitu.

## Trzy scenariusze, 4 runy/miesiąc

Scenariusz konserwatywny pod względem zakresu („oszczędny”) zakłada wysokie reuse i mało poprawek; „oczekiwany” to punkt planistyczny; „drogi” obejmuje więcej scen, high images i poprawki. Żaden nie jest ofertą ani obietnicą jakości. Konserwatywny budżet operatora powinien korzystać z drogiego scenariusza i jawnego cap, nie traktować oszczędnego jako bezpiecznej granicy.

| Parametr / koszt na run USD | Oszczędny | Oczekiwany | Drogi |
| --- | --- | --- | --- |
| Images N/quality/attempt multiplier | 30/low/1.1 | 60/medium/1.25 | 120/high/1.5 |
| Output images | 0.165 | 3.075 | 29.70 |
| Prompt input (500 tokens/image, z attempts) | 0.083 | 0.188 | 0.450 |
| Text Tin/Tout; model $1/$5 per M | 20k/5k: 0.045 | 50k/15k: 0.125 | 200k/60k: 0.500 |
| Speech text/audio tokens | 2k/12k: 0.145 | 3k/15k: 0.182 | 6k/50k: 0.604 |
| Compute vCPU/GiB/seconds | 2/4/1800 | 2/8/3600 | 4/16/10800 |
| Compute przy założonych sufitach | 0.137 | 0.331 | 1.987 |
| Razem zmienne generacja+batch/run | 0.575 | 3.901 | 33.241 |
| Razem ×4/miesiąc | 2.30 | 15.60 | 132.96 |

Prompt rounding jest do trzech miejsc, totals do dwóch. TTS kolumna obejmuje założony łączny token usage także poprawek, bez dodatkowego mnożnika. Nie obejmuje replacement Realtime: [deprecations](https://developers.openai.com/api/docs/deprecations) podaje shutdown obecnego Speech 2027-01-06; przed produkcją długoterminową wycenić nowy adapter w CLOUD-031A. Research z paid search nie jest w tych kosztach — MVP approved topics; przy włączeniu dodać tool calls i content tokens.

| Pozostałe pozycje miesięczne | Oszczędny | Oczekiwany | Drogi | Sposób rozliczenia / ograniczenie |
| --- | --- | --- | --- | --- |
| GCS średnia retencja, przykład 3 mies. | 12 GiB: 0.24–0.48 | 60 GiB: 1.20–2.40 | 240 GiB: 4.80–9.60 | Założona stawka, bez automatycznego kasowania historii; storage rośnie bez retencji |
| Internet transfer incl. PC/preview | 4 GiB: 0.48–0.80 | 12 GiB: 1.44–2.40 | 40 GiB: 4.80–8.00 | Retry i Drive mirror zwiększają bytes |
| Firestore + storage operations | rezerwa 0.10 | rezerwa 0.50 | rezerwa 2.00 | Zastąpić reads/writes/size pomiarem; brak Tasks: 0 |
| API + hourly reconcile compute | rezerwa 4.20 | rezerwa 5.00 | rezerwa 11.00 | minimum 1 min/Job; około 720 ticków/mies., profil 2/8 lub 4/16; API min=0 |
| Scheduler | rezerwa 0.20 | rezerwa 0.20 | rezerwa 0.20 | [Cennik](https://cloud.google.com/scheduler/pricing): $0.10/job/31 dni przed darmowym limitem; hourly + opcjonalny weekly |
| Registry/build/logging/secrets/backups | rezerwa 1–3 | rezerwa 2–6 | rezerwa 5–15 | Oddzielne SKU/retencja; scan i CI minutes również mogą kosztować |
| Google Drive | 0 gdy disabled | 0 gdy disabled | 0 gdy disabled | Opcjonalny plan użytkownika/quota i duplikat storage poza tym modelem |
| Suma orientacyjna incl. powyższe | około 9–12 | około 26–33 | około 161–183 | USD/miesiąc; szacunek, przed live billing calibration |

Ważny narzut: [Jobs billing](https://cloud.google.com/run/pricing) nalicza minimum 1 minutę na instancję. Przy 2 vCPU/8 GiB i założonych sufitach hourly tick kosztuje `720 × 60 × (2×0.000030 + 8×0.000004) ≈ $3.97/mies.` przed free tier. Tick co 5 minut to około $47.69/mies. przy tym samym profilu. Dlatego baseline stosuje hourly recovery; dokładny weekly start można dodać drugim Scheduler job tego samego Run Job. Szybszy wymagany SLO uzasadnia oddzielnie wyceniony krótki API reconcile zamiast ukrytego narzutu batch polling.

Koszt stały/operacyjny: retained GCS/Registry/backup, aktywne secret versions i Scheduler; zmienny: paid requests, compute, transfer, operacje DB. [Secret Manager](https://cloud.google.com/secret-manager/pricing) zależy od aktywnych wersji i accesses; nie zakładać bezlimitowych rotacji. Brak płatnego Drive planu nie znaczy nieograniczonej przestrzeni. Nie policzono energii/amortyzacji PC, pracy operatora, domeny, podatków, opcjonalnego LB/VPC/Monitoring alerts, ponadlimitowych logów lub skanera. WIF nie wymaga długowiecznych SA keys. YouTube quota nie jest budżetem OpenAI; brak obietnicy monetyzacji.

N kanałów: `N × 4 × C_run + shared fixed + storage(N, retention) + transfer(N)`; auth/UI/deployment pozostają wspólne. 3-miesięczny przykład storage nie jest rekomendacją kasowania źródeł; bez akceptowanego retention cap forecast musi pokazać narastanie roczne.

## Kontrola kosztów i operacje

Każdy request ma estimate/model/pricing revision, atomową reserve w ledger i receipt: estimated/reserved/known_actual/billing_unknown. Known actual tylko gdy istnieje provider usage/invoice reconciliation; bytes/liczba znaków to estymata. Jednoczesne kanały nie mogą przekroczyć miesięcznego cap. Billing budgets/alerts GCP są dodatkową obserwacją, nie twardym stop. Fail closed po pricing expiry, rate/quota/permanent auth i wyczerpaniu attempts. Awaria po dispatch nie zwalnia rezerwy automatycznie.

Cache i reuse: pin section revisions, prompts/model/voice/seed settings, source image hashes, motion/renderer profile, alignment identity; naprawiać tylko zmienione sekcje/Shorts. Najpierw zweryfikować istniejące responses/chunks, potem rozważać płatny retry. Nie używać osobnej generacji 3 Shorts w pierwszej migracji. Logi ograniczone, bez pełnych promptów/tokenów/URLs; media transfer streaming. Dashboard pokazuje per run/channel/month estymaty, rezerwy i unknown obok znanych actual.

Runbook awarii: zatrzymać nowe paid dispatch; sprawdzić lease/epoch i ostatni committed manifest, odzyskać receipt/snapshot, ponowić tylko bezpieczny etap z tym samym fingerprint. Worker missing → awaiting_local/alert/fallback po decyzji. OAuth revoked → blocked_credentials/reauthorize, bez ponownego upload. Ambiguous YouTube → reconcile istniejącą sesję/ID; bez nowego insert. Restore do nowego bucket/database/workspace z zachowaniem IDs i zweryfikowanym katalogiem dependencies; po restore revalidate approvals i upload ledger przed wznowieniem.

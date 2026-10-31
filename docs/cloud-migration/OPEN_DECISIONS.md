# ARCH-001 — OPEN_DECISIONS / PROPOSED ADR

**Status wszystkich pozycji:** `PROPOSED`, wymagają zatwierdzenia właściciela. **Nie zatwierdzono żadnego ADR i nie zmieniono `ADR 0003`.**

| ID | Decyzja do zatwierdzenia | Rekomendacja | Rozważone alternatywy i dlaczego nie teraz | Impact / gate |
| --- | --- | --- | --- | --- |
| DEC-01 | UI | **React + TypeScript + Vite**, statyczny bundle serwowany przez FastAPI, jeden deployable UI/API | Svelte: trochę mniejszy bundle, ale drugi ekosystem; HTMX: świetny dla formularzy, słabszy przy job state/progress/preview/review | Akceptacja przed UI-001 |
| DEC-02 | Kształt backendu | **Modułowy monolit** ze współdzielonym Core i adapterami | Microservices: nadmiar koszów, deploymentów i transakcji | Przed CORE-001 |
| DEC-03 | Granica deploy | **Jeden Cloud Run Service** (UI/API) + **jedna definicja Cloud Run Job** (runner) w jednym środowisku | Long-running HTTP, osobne UI hosting/SPA, klaster Kubernetes | Przed DEPLOY-001 |
| DEC-04 | Job orchestration | API tworzy durable record; uruchamia **Cloud Run Job execution**, runner idempotentny; reconciler dla luk | Cloud Tasks i własna kolejka od dnia 1: więcej komponentów; Cloud Scheduler: dopiero później | Przed API-001/DEPLOY-001 |
| DEC-05 | Metadata | **Firestore** production/jobs/attempts/approvals/leases, GCS na artefakty | Cloud SQL: koszt operacyjny dla 1 filmu/tydz.; GCS-only JSON: słaba konkurencja i query; rozproszony SQLite: niedopuszczalny | Przed STORAGE-001 |
| DEC-06 | Projekt i historia | **Immutable production snapshots**, mapa legacy ID, GCS checkpoints; stary SQLite read-only/migrate explicit | Przepisać cały model SQLite w jednej iteracji: wysoki risk | Przed STORAGE-001 |
| DEC-07 | Portable format | **`production-package/v1`** manifest JSON + SHA256 + relative paths + frozen identities/timing | Sam MP4 lub zip bez historii: strata zdolności final render | Przed CORE-002B |
| DEC-08 | Cloud/local handoff | **GCS source package → signed/time-limited download lub authenticated CLI → verified local cache** | Sieciowy filesystem z Windows, ręczne kopiowanie bez manifestu | Przed LOCAL-001 |
| DEC-09 | Preview | **FFmpeg `proxy=True` 640×360 / 360×640, 25 FPS**, source images oddzielne | Upscale MP4 w chmurze / 1080p cloud render: koszt i słabsze źródło | Przed CORE-002A |
| DEC-10 | Upscale | **Upscale źródeł** → motion masters → final render na Windows GPU | Upscale final MP4: przepala kompresję, napisy i obraz bez rekonstrukcji motion; fallback tylko eksperymentalny | Przed LOCAL-001 |
| DEC-11 | Shorts | **Po pierwszym cloud MVP**, plan metadanych wcześniej, realne Shorts po zweryfikowanym finalnym źródle; osobny spike proxy-compat | Natychmiast renderuj przez istniejący ShortsService na proxy: niezgodne source checks | Przed SHORTS-001 |
| DEC-12 | Desktop retirement | **Compatibility freeze**, bez usuwania UI do potwierdzenia end-to-end + legacy projects | Natychmiastowy delete: ryzyko utraty historii | Przed CLEANUP-001 |
| DEC-13 | GCP scope | **Jedno środowisko** (`europe-central2` do potwierdzenia możliwości usług), least privilege, private UI, Secret Manager, Artifact Registry, GCS | Kopiowanie DEV+PROD z Tripse: niepotrzebne | Przed DEPLOY-001 |
| DEC-14 | CI/CD | WIF, pinned digests, fail-closed deploy, smoke, rollback; **minimalna kopia wzorców Tripse**, nie deploy template 1:1 | Cały pipeline Tripse z DEV/PROD promotion: zbyt złożony | Przed DEPLOY-001 |
| DEC-15 | Spending/recovery | **Preauthorized cap per production + per-provider hard reservation, max attempts, reconcile unknown outcomes**; manual explicit retry | GUI popup w trakcie headless albo nieskończony retry | Przed CLOUD-PAID-001 |
| DEC-16 | Dostęp do UI | **Private/IAP** lub inne zgodne auth; żaden publiczny niezabezpieczony endpoint generacji | Publiczna FastAPI z API key w frontend: niedopuszczalne | Przed DEPLOY-001 |
| DEC-17 | Plan legacy i nowy ADR | Stworzyć **nowy ADR 0004 cloud/hybrid** (numer po sprawdzeniu kolizji) o statusie Proposed → Accepted dopiero na decyzję | Edycja/dziedziczne nadpisanie `ADR 0003`: utrata historii | Pierwszy governance gate |
| DEC-18 | Pierwsza paczka Shorts | Start production: 1 long form, 0 Shorts; metadata 3 Shorts później | Wymusić 3 Shorts już w pierwszym GCP job: blokada pionowego MVP | Przed CORE-002 |

## Planning gates — all PROPOSED

- **DEC-01 / DEC-15:** React/UI-001 is independent of the first controlled live OpenAI smoke. Local headless, real offline proxy/package and persistent GCP job execution precede manually approved, budget-limited CLI/API smoke. Real OpenAI calls remain disabled during ARCH-001.
- **DEC-05 / DEC-06:** incremental storage: portable manifest → basic GCS → persistent production metadata → full leases/checkpoints/recovery. Earlier controlled runs block after interruption for manual reconciliation. Full safe recovery is required before unattended scheduling; avoid an upfront distributed transaction framework.
- **Acceptance:** short synthetic 30–90 s production smoke for regular offline CI; full approximately 10-minute acceptance separately, more expensive and opt-in. Live providers always require separate budget approval. Full acceptance and recovery hardening precede unattended scheduling.

## Projekt ADR — do odrębnego PR po akceptacji

**Title:** `ADR 0004 — Cloud-first AI Content Studio with local GPU finishing` (`0004` tylko pod warunkiem wolnego numeru).

**Status:** `PROPOSED`.

**Context:** wymagania przesunęły się z ręcznego, lokalnego edytora w stronę bezobsługowej produkcji; istniejące kontrakty rewizji/artefaktów/FFmpeg pozostają wartościowe.

**Decision:** UI/API Cloud Run Service, produkcja Cloud Run Job, GCS/Firestore, shared core bez Qt/GCP SDK importów, Windows GPU final worker, kompatybilność starych projektów, presety zamiast edytora, lokalny code freeze i etapowy retirement.

**Consequences:** płatne API i cloud recovery wymagają jawnych limitów, GCS snapshotów i dokładnej ewidencji; nowy UI nie daje pełnych możliwości edytorskich; infrastruktura jest prostsza niż komplet Tripse, ale dodaje bezpieczeństwo i logowanie.

**Proposed supersession only (no approval in ARCH-001):** ADR 0003 pozostaje obowiązującą zaakceptowaną decyzją desktopową. Nowy ADR, wyłącznie jeśli zostanie odrębnie zaakceptowany, **would supersede product orientation for future cloud/hybrid migration only**, bez zmiany znaczenia starych D### i bez uznania code paths za ukończone. `docs/desktop/IMPLEMENTATION_PLAN.md` wciąż jest źródłem prawdy o desktop D###; `docs/cloud-migration/MIGRATION_BACKLOG.md` reguluje nowe zadania dopiero po akceptacji ADR.

## Pytania właścicielskie (przed pierwszym płatnym GCP run)

- Jaki maksymalny koszt **całej produkcji** i oddzielnie Images/Speech/LLM? Jaki mechanizm, gdy preflight nie umie oszacować górnej granicy? Domyślnie **BLOCK**, bez płatnego dispatch.
- Czy pojedynczy operator wystarczy na pierwsze 2–3 miesiące? Domyślnie prywatny dostęp i jeden konto-operator; bez zaproszeń publicznych.
- Czy historyczne desktop projekty wymagają migracji do chmury, czy tylko bezpiecznego archiwum i czytnika? Domyślnie **archiwum/read-only, migration on demand**.
- Czy Shorts v1 mają być **Original Excerpt** po finalizacji GPU, czy bardziej złożone AI Director? Domyślnie Original Excerpt, bez Director.
- Czy źródła z OpenAI mogą być przechowywane długo w GCS? Domyślnie retention policy do późniejszego zatwierdzenia, bez automatycznego usuwania dzieł niezatwierdzonych.

## Procedura akceptacji

Zatwierdzenie DEC-01–18 powinno mieć ślad w ADR PR/commit. Sama publikacja tego dokumentu nie oznacza zaakceptowania. Wszystkie decyzje o IAM, cennikach, prywatności, live API calls i storage lifecycle wymagają osobnego operacyjnego przeglądu.

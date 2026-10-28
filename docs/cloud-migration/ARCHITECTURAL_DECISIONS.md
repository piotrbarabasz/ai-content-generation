# Proponowane decyzje architektoniczne

Data: 2026-10-28. Wszystkie rekomendacje: **PROPOSED**. ACCEPTED można wpisać dopiero po decyzji właściciela z datą i zakresem. ADR-0003 nadal obowiązuje. [Projekt ADR-0004](ADR-0004-PROPOSED.md).

| ID / pytanie | Rekomendacja | Alternatywy i uzasadnienie | Tradeoff, ryzyko i dalsza migracja |
| --- | --- | --- | --- |
| CA-01: monolit? | Modularny monolit, wspólne application services | Mikroserwisy zwiększają obsługę i kontrakty sieciowe przy 4 runach/miesiąc | Wspólny release core wymaga compatibility manifestów; wydzielać po pomiarze |
| CA-02: API/UI razem? | Jeden prywatny Cloud Run Service | Osobny frontend to auth/CORS/deployment bez istotnej korzyści | Skalują się razem; duże media w GCS, opcja późniejszego split przez API v2 |
| CA-03: frontend? | Jinja2 + lokalnie przypięty HTMX + mały JS | Static HTML/JS wymaga ręcznej obsługi stanu/formularzy; Svelte/Vite dobry dla pełnego edytora, ale dodaje toolchain i state management; Angular zbędny | Mniej interaktywności niż SPA; video/progress potrzebują JS. Svelte możliwy później |
| CA-04: orkiestracja? | Jeden resumable Job, checkpointy per stage/Short, hourly Scheduler reconcile | Cloud Tasks dla krótkich actions; Workflows dla różnych compute classes; dziś brak potrzeby | Sekwencyjność i recovery do około godziny; minimum minuty billing Jobs uwzględnione w kosztach; checkpointy umożliwiają późniejszy split |
| CA-05: baza? | Firestore Native regional dla control plane; prywatny scratch SQLite ze snapshotami | Cloud SQL/Postgres lepszy dla relacji i concurrent editor, lecz stały koszt; shared SQLite wykluczone; GCS JSON CAS niewygodne dla lease/budżet/query | Brak transakcji między GCS i Firestore: immutable upload + CAS; wymieniać repositories kolejno, jeśli snapshoty staną się drogie |
| CA-06: GCS czy Drive? | GCS primary; Drive export DEFERRED | Sync folder Drive nie daje commit/lock i może zawierać partial/stale pliki | Więcej własnej obsługi transferu, ale generation/hash i scoped grants; mirror później |
| CA-07: gdzie render? | Cloud preview, local final; MVP cloud final FHD | Całość local blokuje podgląd, całość cloud zwiększa scratch/compute | Wersje FFmpeg/fonts i timing muszą być zgodne; nie czekać Jobem na PC |
| CA-08: neural upscale? | Source images przed final render | MP4 neural SR wymaga nowego adaptera i temporal QA; FFmpeg resize to resampling | Więcej assetów, ale dziesiątki obrazów zamiast tysięcy frames; 4K po benchmarku |
| CA-09: kto publikuje? | Cloud publisher po powrocie final assets | Local upload rozprasza OAuth/ledger i zależy od PC | Dodatkowy transfer GCS, lecz jedna kontrola tokenów i duplikacji; transport za portem |
| CA-10: unattended budżet? | Uprzedni policy revision, run/channel/month caps, reserve per attempt, pricing expiry | Blanket approve i billing alerts nie blokują wydatków; dialog per call uniemożliwia scheduled | Ostrożna estymata może zatrzymać run; billing_unknown nie zwalnia rezerwy i wymaga reconciliation |
| CA-11: kanały/schedule? | Wersjonowany ChannelProfile i Schedule, run pin i unique UTC slot | Mutable global settings niszczą odtwarzalność; deployment per channel zbędny | DST/catch-up jawne; owner/channel scope; skalowanie przez concurrency/fairness |
| CA-12: desktop? | Zachować local editor, export/import do nowej kopii | Usunięcie GUI zabiera działające narzędzia; dwukierunkowy live SQLite sync ryzykowny | Bez collaborative editing MVP; konflikty ancestry wymagają fork/rebase |
| CA-13: dokumentacja? | Nowy ADR i jawne authority CLOUD/D### | Cicha edycja ADR-0003 zaciera historię | CLOUD-001 zatwierdza kierunek, nie wykonanie całego backlogu; potem jedno wybrane zadanie |
| CA-14: wiele kanałów? | Wspólne compute, channel-scoped leases/budgets/config | Wczesny SaaS i deployment per tenant nieuzasadnione | Globalny rate limiter, początkowo jeden paid run naraz; osobne projekty dopiero dla realnej izolacji |
| CA-15: dashboard auth? | Direct IAP, signed JWT validation, app roles i CSRF | App OIDC/Identity Platform to więcej obsługi sesji; API key nie jest dashboard login | External owner/programmatic access wymagają bootstrap/testu; nie zakładać sukcesu z Tripse |
| CA-16: worker auth? | Dedykowany Google user principal, programmatic IAP OAuth, refresh w Windows secure store, krótkie tokens | WIF wymaga realnego external IdP; bez SA keys; device pairing wymaga oddzielnej decyzji | Realny test unattended refresh obowiązkowy. Nie używać owner token z pełnymi prawami |
| CA-17: audio lifecycle? | Oddzielna ocena replacement przed długoterminowym rollout | Aktualny Speech model ma ogłoszony shutdown; Realtime to inny kontrakt, nie tylko nazwa modelu | CLOUD-031A: chunk/timing/PCM/cache/cost i voice equivalence; brak automatycznego fallback |

## Decyzje właściciela i bramki

CLOUD-001: kierunek cloud-first/hybrid, zastąpienie części ADR-0003, authority CLOUD/D###, jedno środowisko i snapshot workspace. Do ustalenia: primary finishing/fallback, tożsamość workera, modele/voice, owner kanału i kwoty budżetów. Brak project ID nie blokuje offline foundation, blokuje provisioning.

Przed scheduling: jawne run/month caps, retry/expiry policy, zakres autoaccept scen/script, recovery proof, private upload i aktualny model audio. Przed publikacją: approve exact final manifest/QA/metadata/disclosure i zweryfikowany kanał. Audyt nie udziela tych zgód.

DEFERRED: direct TikTok, niezależne Shorts, neural video upscale, 4K default, pełny web editor, multi-user collaboration, Tasks, microservices, drugie środowisko, Drive mirror. Nie są zależnościami krytycznej ścieżki.

Niezweryfikowane: docelowy IAM/OAuth, refresh workera bez browsera, dostęp modeli na koncie, RAM/scratch 10-min filmu, rzeczywisty usage i łącze PC. Każda niewiadoma ma zadanie/bramkę w planie.

Lifecycle audio sprawdzono 2026-10-28: [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) zapowiada usunięcie `gpt-4o-mini-tts` 2027-01-06 i wskazuje `gpt-realtime-2.1-mini`. Istniejący adapter Speech nie obsłuży tego przez samą podmianę model ID. Kod już zawiera SHUTDOWN=2027-01-06 i fail-closed check_configuration; plan uwzględnia tę istniejącą granicę.

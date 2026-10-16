# D079 — Narration attempt eligibility and recovery

Implemented on `fix/openai-narration-attempt-recovery`, based on D078 (`381e6f0`).
No paid API requests, counter resets, or changes to the real project's scripts,
images, databases or media were authorized or performed during this fix.

## Investigated failure

Project: **What If the Soviet Union Landed on the Moon First?**
The user reported 8 completed script groups, 16 sections and 12 retained sections.
Read-only inspection independently verified the 12 selected raw WAV checksums and
PCM, plus the frozen failed section's exact indexed job, technical manifest and
narration records. `project.sqlite` was exclusively locked by the open app; it was
not unlocked, migrated, opened through a writable session or copied inconsistently.
Consequently the investigation does not claim an independently read current-script
snapshot. The read-only tool labels this mode `frozen_job_only`.

The blocked source is **Long-Term Trajectories**, technical chunk
`sentence-chunk-0001-772464a87505` (second chunk), response key beginning
`c35e67481753bfae9800300b0fa50d466` and scope beginning `67db9fcdd0d7e8e8`.
Its first chunk is complete: 265200 frames / 24000 Hz = **11.05 seconds**, checksum
beginning `50c3934b50f91fe4`. The failed chunk has no verified WAV and no retained
complete or partial response in the inspected response directory.

Both reservations belong to the original approval, with estimated reserves of
$0.0110518909 each; total $0.0221037818. These are **not invoices or proof of billing**.

| Reservation | Recorded time (UTC, 2026-10-16) | Failure record | Available evidence |
| --- | --- | --- | --- |
| 1 | 14:25:33.476891 | 14:26:05.566169 | Generic `SPEECH_NETWORK_OR_PROVIDER_FAILURE`, response uncertain |
| 2 | 14:26:07.774766 | 14:26:41.658508 | Same generic category, response uncertain |

The approximately 32–34 second intervals are consistent with the configured
30-second timeout. They **do not prove** a timeout, a server error or successful
provider billing. D078 converted every unknown SDK/application exception without
an HTTP status into the same retryable network/provider category. It discarded
the original exception type, phase and received byte count. No successful receipt
or retained response can now be reconstructed from those reservations. There is
no retained evidence of a WAV validation failure for this chunk; such an error
would normally occur after raw response retention. A crash before retention or
external deletion also cannot be excluded from these historical records.

The repeatable application defect is established: `estimate` counted missing
sections but never checked chunk eligibility. A new ordinary approval could not
reset `reserve`'s lifetime count, yet the UI kept offering it. Each resume failed
again without an API call and wrote another misleading `failed_or_uncertain`
record. The technical manifest's **10 invocations do not mean 10 paid requests**;
only two reservations exist for this exact scope/key. Historical records remain
unchanged; they are displayed as unlinked legacy evidence rather than fabricated
per-reservation outcomes.

## Behavior after this fix

Read-only preflight classifies every required technical chunk:

| State | Meaning |
| --- | --- |
| `VALID_CACHED` | Exact frozen section/job, source span, synthesis identity, manifest, checksum and canonical PCM verified |
| `RECOVERABLE_RESPONSE` | Retained complete response verified against the expected text/identity key and checksum, then decoded/validated |
| `ELIGIBLE_FOR_GENERATION` | A paid attempt remains available, configuration is usable and spending limits permit the work |
| `ATTEMPTS_EXHAUSTED` | No usable retained audio and no remaining authorized attempt |
| `BLOCKED_CONFIGURATION` | Missing configuration, input/pricing issue or insufficient project spending ceiling |

A blocked preflight stops Automatic at **VOICE** with the affected section/chunk,
failure category, retained-section count and whether another call would be needed.
An early read-only check runs before plan/context/script work for existing saved
sections; a fresh check runs again before spending approval. A blocked scan never
opens the ordinary payment dialog. Only eligible missing work contributes
to the quote. Verified retained work needs no paid approval. Source identity and
all completed section selections remain unchanged. A successful current section
is an idempotent no-op when resumed again.

**Audio > Scan narration recovery** reads responses and manifests without changing
them. Complete archives/cache pointers, interrupted atomic writes and verified
technical WAVs can be recovered. Partial streams are retained separately for
inspection and never treated as complete receipts, even if their bytes happen to
form a decodable WAV. Recovered audio does not create a fictitious successful
provider-attempt record. Publication still uses the ordinary section job and gate.

An operator may explicitly review **one additional attempt** after the original
two reservations. The review requires an operator name, reason, uncertain-billing
acknowledgement and an estimated-spend ceiling. A new append-only authorization
binds the exact chunk/scope, prior reservation IDs, pricing and evidence fingerprint.
Changed evidence requires a fresh scan. This permission starts no request; normal
Run / Resume still reviews the eligible remaining-operation quote. Dispatch must
satisfy the ordinary operation ceiling, exceptional ceiling and project ceiling.
The exceptional request has no automatic retry, including after 429/timeout. If it
fails, the chunk stops at three reservations and no further exceptional grant is
offered. Original approvals, failed reservations and estimated reserves are retained.

New diagnostics distinguish SDK timeout/connection/HTTP failures from application
errors, retention errors and WAV validation failures. They retain reservation ID,
phase, exception class, status, received bytes, completion state and a conservative
billing-uncertainty flag. Provider bodies, exception messages and credentials are
not logged. An ordinary pre-dispatch denial creates no provider-failure/receipt
record. Completed bytes are archived before SDK context cleanup so a cleanup error
cannot discard otherwise recoverable audio. The categories follow the
[official OpenAI error documentation](https://developers.openai.com/api/docs/guides/error-codes#python-library-error-types);
retry eligibility and spending authorization remain application responsibilities.

## Exact operator steps for the current project

1. Stop Automatic, wait for any in-flight request to finish/cancel, then close the
   app. Restart from this branch with `python run_app.py` so the new code is loaded.
   Keep the script, model, voice, native speed and delivery settings unchanged;
   changing them would invalidate reusable narration instead of fixing eligibility.
2. Open **What If the Soviet Union Landed on the Moon First?**, go to **Audio** and
   click **Scan narration recovery**. The full current-script scan can now verify
   the reported 12 retained / 4 unfinished sections. Check the affected second
   chunk of **Long-Term Trajectories** and the old uncertain outcomes.
3. If a valid retained response/technical WAV has become available, close the scan
   without authorizing an exception and use **Run / Resume**. Verified recovery is
   free; the quote covers only other eligible missing chunks.
4. If no response exists, review the connection/proxy/service condition before
   considering another request. The old records cannot establish the exact
   transport cause. Select this exhausted chunk in the recovery dialog, enter the
   operator and review reason, acknowledge unknown prior charges, and approve
   **one** additional estimated attempt. For the unchanged source/settings, its
   estimate is $0.0110518909; the dialog rounds its default ceiling up to **$0.012**.
   This action authorizes recovery but starts no API call.
5. Click **Run / Resume** and review the new eligible remaining-work quote. Approve
   only a budget you accept. The 12 verified sections are reused; the completed
   first technical chunk of Long-Term Trajectories is reused too. Only missing
   eligible chunks dispatch requests. Continue through measured D067/D077 checks;
   this fix does not alter duration thresholds, editorial text or images.
6. If the reviewed additional request fails, stop and inspect the newly recorded
   category/phase. Do not keep approving ordinary budgets, delete caches/ledgers,
   reset counters or change narration settings merely to evade the bound. There is
   no fourth automatic request for that immutable chunk. Preserve the evidence for
   further operator/provider investigation.

Optional read-only report after closing the app (exit code 2 means blocked, not
that anything was modified):

```powershell
python -m app.tooling.openai_narration_recovery "projects/What-If-the-Soviet-Union-Landed-on-the-Moon-First" > .tmp/moon-narration-recovery.json
```

`--job-id <indexed-job-id>` can inspect a frozen job while the project is open;
it explicitly cannot certify the current script. The tool uses SQLite `mode=ro`
and `query_only`, never `ProjectSession.open`, queue recovery or cache writes. It
validates indexed artifact checksums and ownership before scanning source data.
The complete real diagnostic remains ignored in `.tmp/d079-current-report.json`;
no private script text, generated media or raw response bytes are committed.

## Validation

All automated fixtures use temporary workspaces, synthetic PCM and mocked SDKs.
The tests cover exhausted preflights/no payment dialog, ordinary denial without
fake outcomes, retained response and technical recovery, wrong provenance,
interrupted download, SDK cleanup/application errors, explicit reviewed grants,
one-call bounds, stale evidence, operation/project ceilings, idempotent resume,
cancellation, unaffected audio/image preservation and Automatic error context.
The full backend suite passed: **2544 passed, 12 skipped in 2033.59 seconds**.
Final SHA-256 verification matched all **394 original project files**, including
its databases, scripts and media. All 12 selected narration WAVs were independently
verified during the read-only investigation. No paid API request was made.
Focused coverage passed 111 tests; the final early-preflight/Automatic checks
passed 21 tests, and an additional targeted check verified that following missing
sections resume independently after the blocked section is recovered.

# D017 — Image generation contract and mock

## Implementation boundary

Provide a small text-to-image request/result/capability contract and an explicit,
lazy factory with one deterministic PNG mock. No real model/API, img2img, search,
character consistency, provider enum migration or universal media union is added.
The mock produces a patterned RGB fixture, not semantic illustrations.

Pin the selected D015 prompt revision and selection event, section revision,
dimensions, format, seed, negative prompt and effective provider capabilities.
Normal enqueue can reuse the latest validated matching candidate. `force=True`
always reserves a new D006 job/D040 generation even when bytes are deterministic.
Pending work is not a cache hit. Retry retains its original job inputs; completion
replay does not call the provider again. Provider identity drift is rejected.
Every v1 request pins a 32-bit seed; providers that do not support seeded requests
are rejected. The mock accepts PNG up to 1024 per axis and 1,048,576 pixels. These
capabilities are checked before enqueue; output bytes are separately decoded with
the existing D016 limits and compared against requested and reported measurements.

Reuse D016 decoding/measurements and immutable image selections, adding generated
provenance without changing imported payloads. D040 publishes a generated candidate
and completes its job atomically. Its candidate head is not the user's scene-image
choice: importing/generating/cache hits never implicitly change that choice.
Old imported or generated variants can be selected explicitly with D016's expected
selection event token, retaining all earlier variants and audio.

D015 prompt choices live in immutable event chains, not D040 head rows. A scoped
`ImageResultIndex` extension must therefore compare the pinned prompt selection
inside the D040 transaction as well as its existing revision/generation checks.
This closes the late-prompt-change boundary without changing other operations or
the database schema. Valid late results are retained with an obsolete decision;
they are not reused as current cache results. Explicit historical variant choice
remains available while its section revision is current.

Run the synchronous mock on the owning coordinator. Real worker/provider runtime
composition remains outside D017. Extend the private worker's source closure for
the pure contract imported by `providers.interfaces`; do not bundle the mock or
load its provider when image generation is disabled.

## Validation

Windows / isolated `.venv-ci311`, Python 3.11.9, 2026-09-15. No new dependencies.

- D015/D016/D040 baseline: **104 passed** in 53.60 s.
- Compatibility suite (image contracts/generation, D015 prompts, D016 intake,
  D040 publication and private-worker source imports): **173 passed** in 105.13 s.
- Final D017 focused suite, including the cache revalidation regression:
  **59 passed** in 79.08 s. These two new test files contain all 59 new cases.
- Full `python -m pytest backend/tests`: **1263 passed, 11 skipped** in 351.06 s.
  All skips are existing D044 Windows symlink privilege cases (`WinError 1314`);
  no new D017 case skipped.
- `git diff --check`, UTF-8/whitespace/EOF, evidence-link and task-status scope: PASS.

Final focused command:

```text
python -m pytest backend/tests/unit/test_image_generation_contract.py backend/tests/integration/test_image_generation.py
```

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Mock produces decodable image bytes | Independently decode generated PNG and compare measured dimensions/format. Identical requests produce identical bytes; changed seed/settings change request identity. Injected JPEG fixture works through the same application/publication path. | PASS |
| Force creates a new generation record | Matching normal request reuses its completed artifact without a job/provider call; forced request creates distinct job, generation and artifact IDs with the same frozen request fingerprint. Retry preserves the original job; replay does not run the provider. | PASS |
| Selecting an old variant changes no audio | Explicitly choose new then old generated variants using D016 selection events; every previous artifact byte and audio remains unchanged. Selection works with generation disabled. Imported choices and manual choices made during generation survive late completion. | PASS |

Stale section, prompt switch/ABA, prompt change immediately before commit and
superseding generation tests retain obsolete results without changing the current
candidate head. Invalid contract/PNG/format/dimensions, capability/identity drift,
cancellation and transaction failures cannot replace the previous image choice.
Cleanup failure after the index commit reports the committed outcome. Reopening
retains generation history, explicit image choices and cache identity. A corrupt
candidate is not a cache hit. No real model/API, network or private image is used.

## Composition and use

```python
from app.application.image_generation import ImageGenerationService
from app.application.result_publication import ResultPublicationService
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.image_factory import build_image_provider
from app.storage.image_generation import ImageResultIndex, ProjectImageGeneration
from app.storage.local_store import LocalArtifactStore

jobs = JobRepository(session.repository)
index = ImageResultIndex(session.repository, jobs)
store = LocalArtifactStore(index.root, index=index)
store.recovery_report = store.recover()
artifacts = ProjectImageGeneration(index, store)
coordinator = JobCoordinator(jobs)
service = ImageGenerationService(
    ResultPublicationService(index, store), coordinator, artifacts,
    build_image_provider("mock"),
)
submission = service.enqueue(selected_prompt_revision_id, width=512, height=512, seed=42)
```

`submission.cached_artifact_id` identifies a cache hit. Otherwise `submission.attempt`
is a queued D006 job. The coordinator dispatcher passes the matching running image
claim to `service.run(claim)`; other queued operation types retain their own handlers.
The resulting `PublicationResult.selected_at_publication` describes the candidate
head only. To choose a variant for a scene, call `service.select(artifact_id,
expected_selection_id=previous_choice_id)`; use `None` for the first choice.
`artifacts.images.history(scene_id)` and `index.history("scene:" + scene_id +
":image_candidate")` retain media and generation decisions respectively.

## Changed files

- `backend/app/providers/image_generation.py`: pure request/result/capability protocol.
- `backend/app/providers/interfaces.py`: image protocol entrypoint.
- `backend/app/providers/mock_image.py`: deterministic standard-library PNG mock.
- `backend/app/providers/image_factory.py`: lazy explicit composition.
- `backend/app/application/image_generation.py`: enqueue/cache/run/select service.
- `backend/app/storage/image_generation.py`: prompt-aware D040 index and media validation.
- `backend/app/domain/scene_image.py`: generated provenance alongside unchanged imports.
- `backend/app/runtime/worker_bundle.py`: pure contract in private-worker source closure.
- `backend/tests/unit/test_image_generation_contract.py`: contract/mock/capability tests.
- `backend/tests/integration/test_image_generation.py`: jobs, cache, variants and failure tests.
- `docs/desktop/D017_IMAGE_GENERATION.md`, `docs/desktop/IMPLEMENTATION_PLAN.md`: boundary/evidence.

Branch: `feat/d017-image-generation-contract`. No D018 work, commit, push or merge.

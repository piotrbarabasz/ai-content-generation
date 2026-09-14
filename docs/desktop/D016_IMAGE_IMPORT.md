# D016 — Controlled image import

## Boundary and decisions

Use an application service with a project image port; the local adapter composes
D013 scene ownership, D044 source path checks and D004 immutable publication.
Import is an explicit caller-selected local file, optionally confined to a supplied
source root. No UI, HTTP route, image generation, search or video support is added.

Pillow is a lazy adapter dependency. Allow only single-frame PNG/JPEG, check
compressed bytes (16 MiB), each axis (8192) and total pixels (32 million), then
verify the container and reopen for full pixel decoding. Reject decoder warnings,
truncation, unsupported formats and animation before publication. Limits can be
lowered for tests/deployments. Preserve exact source bytes including metadata;
only basename, SHA-256, measured format/dimensions/mode and EXIF orientation enter
the public image value. Absolute paths are never persisted. Original embedded
metadata is retained privately with the source; no public image-serving endpoint
or metadata sanitizing/export operation is part of this task.

Images pin project, accepted scene plan, scene and section revision. Import and
selection are separate immutable publications. `import_and_select` imports first,
then appends an explicit selection event with an expected previous selection ID
under the existing exclusive project session. Recheck current scene ownership and
the previous selection at commit time. A failed selection leaves the old choice
and the newly imported, unselected candidate intact. A catalog commit followed by
cleanup failure is reconciled against the exact retained bytes. No schema change,
automatic image replacement, deletion or D017 work is needed.

Decoder behavior follows the primary [Pillow Image reference](https://pillow.readthedocs.io/en/stable/reference/Image.html)
and [security guidance](https://pillow.readthedocs.io/en/stable/handbook/security.html).
In-process limits bound normal decoding; hostile native decoder exploitation and
process-level resource isolation are not claimed here.

## Validation

Windows / isolated `.venv-ci311`, Python 3.11.9, Pillow 12.3.0. Pillow is declared
as `Pillow>=12.3,<13` in both default dependency manifests; imports remain lazy.

- Dependency baseline: 61 passed, 11 skipped.
- Focused tests: **114 passed, 11 skipped** in 34.22 s, including **53 new D016
  cases, all passing**. Run the two image test files plus `test_scene_plans.py`,
  `test_streaming_artifacts.py` and `test_workspace_containment.py`.
- `python -m pip check`: PASS.
- Full `python -m pytest backend/tests`: **1204 passed, 11 skipped** in 227.50 s.
- `git diff --check`, UTF-8/whitespace/EOF, evidence link and task-status scope: PASS.
- The 11 skips are existing D044 symlink capability tests (`WinError 1314`).
  The new D016 Windows junction test passed. No model, image provider, network
  connection or reference media is used in tests; PNG/JPEG fixtures are synthetic.

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Image survives moving/reopening the project | Delete the original source, close and move the project into a Unicode/space path; reopen, resolve selection and compare exact bytes/checksum and metadata for PNG RGB/RGBA/palette and JPEG RGB/grayscale/CMYK. | PASS |
| Reject invalid/oversized decode | Empty/non-image/GIF/BMP/truncated PNG/JPEG/APNG rejected; exact-limit progressive JPEG accepted; byte/axis/pixel limits, warning/error bombs and strict truncation behavior tested before publication. | PASS |
| Prior selected image remains intact on failure | Invalid input, stale scene/token, corrupt candidate and index transaction failure preserve the previous event head and bytes. Post-commit cleanup failure returns the actual committed selection. Other scene selections and audio remain unchanged. | PASS |

## Composition

```python
from app.application.image_intake import ImageIntakeService
from app.storage.local_store import LocalArtifactStore
from app.storage.scene_images import ProjectSceneImages

store = LocalArtifactStore.for_project(session.repository)
images = ProjectSceneImages(session.repository, store)
service = ImageIntakeService(images)
image, choice = service.import_and_select(
    accepted_plan_id, scene_id, selected_file,
    expected_selection_id=None,  # Or the previous choice.id for replacement.
    source_root=selected_directory,
)
```

`import_file` retains a candidate without selecting it. `select` can explicitly
restore an older artifact using the latest selection token. `history` and
`selection_history` expose retained candidates and the selection chain.
Selection of an image tied to an edited section is refused, while historical
image/selection records remain readable. If a selection fails after a successful
import, the candidate remains in history and can be selected explicitly later.

## Changed files

- `backend/app/application/image_intake.py`: injected application service.
- `backend/app/domain/scene_image.py`: image measurements and selection values.
- `backend/app/storage/image_decoder.py`: lazy, bounded PNG/JPEG validation.
- `backend/app/storage/scene_images.py`: project intake, ownership and selection history.
- `backend/tests/unit/test_image_decoder.py`: decoder limits/strictness.
- `backend/tests/integration/test_image_intake.py`: import/selection/failure/relocation behavior.
- `pyproject.toml`, `backend/requirements.txt`: Pillow dependency.
- `docs/desktop/D016_IMAGE_IMPORT.md`, `docs/desktop/IMPLEMENTATION_PLAN.md`: boundary and evidence.

Branch: `feat/d016-controlled-image-import`. No D017 work, commit, push or merge.

# Module contracts

This page describes current modules, not completed desktop pipeline stages.
The [desktop implementation plan](../desktop/IMPLEMENTATION_PLAN.md) defines
section-scoped application operations and the order of the remaining work.

`ModuleDefinition` declares input/output/config schemas, dependencies, default
enabled state, disabled behavior (`skip` or `fail`), retry limit, artifact names
and error behavior. `WorkflowModule.execute(ModuleExecutionContext)` returns a
`ModuleResult`. Inspect each module's definition and behavioral tests when changing
it: schemas are descriptive dictionaries, not a universal schema-validation runtime.

Dependency groups are ANDed; alternatives inside one group are ORed. The registry
resolves an enabled predecessor for every group. Direct calls may also accept
explicit inputs, but that does not bypass registry dependency validation.

| Module | Declared predecessors | Main persisted outputs |
| --- | --- | --- |
| `brief` | None | `brief.json` |
| `research` | None; topic/source inputs | `research.json` |
| `dossier` | `research` | `dossier.json` |
| `outline` | One of dossier/research/brief | `outline.json` |
| `scriptGeneration` | One of outline/dossier/research/brief | `script.txt`, `script.json`, `narrative_segments.json` |
| `postProcessing` | scriptGeneration | `post_processed_script.txt`; preserves original |
| `qa` | postProcessing | `qa_report.json`, optional script-review checkpoint |
| `scenePlanning` | scriptGeneration | `render_scenes.json`, `scene_plan.json`, optional scene-review checkpoint |
| `voiceover` | brief | `voiceover.wav`, `speech_timeline.json`; resumable synthesis/benchmark artifacts when configured |
| `captions` | voiceover or scriptGeneration, plus scenePlanning | `captions.json` and separate SRT output |
| `videoRendering` | scenePlanning | `render.mp4` containing the current provider reference, plus result metadata |
| `export` | No fixed predecessor list; consumes previous results | `manifest.json`, `workflow_config.json`, `workflow_run.json`, conditional references and optional platform handoff |

The default retry limit is one across these modules except voiceover, which has
two retries. Resumable narration also has its own per-chunk retry boundary.
Required versus optional status comes from both preset configuration and module
disabled behavior; do not assume every module can be omitted arbitrarily.

`PublishingModule` exposes `publish`, not the generic `execute`/`definition`
protocol. It is a separate post-export application service, not automatically a
step in the generation preset.

The implementation contains real artifact handling and deterministic foundation
behavior. Mock LLM research is not live source retrieval; rule-based scene
planning is not a semantic visual production pipeline; estimated word timing is
not forced alignment; a mock render reference is not playable MP4. These limits
are addressed by [product roadmap](../ROADMAP.md), without replacing the modules
or removing their tests.

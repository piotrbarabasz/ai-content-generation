# Workflow engine

`CoreWorkflowEngine` is the product's content execution engine. It is independent
of the removed software-development orchestration system.

1. Build a `WorkflowConfig` and compose the configured providers and module instances.
2. Register real `ModuleDefinition` values with `ModuleRegistry`.
3. Build an execution plan from an explicit ordered sequence and disabled modules.
   The registry rejects duplicates, missing predecessors and disabled required steps.
4. Invoke `CoreWorkflowEngine.run` with the plan, run/config IDs, config, provider
   registry and inputs. Supplying config enables provider availability validation.
5. The engine executes enabled steps, captures `ModuleResult`, retries failures up
   to each step's limit, and stops on required failure or pending approval.
6. For continuation, supply prior module results and resolved checkpoints.
   Completed results are reused; unresolved review remains paused.

`run_plan` is a lower-level execution entrypoint. It does not perform the
config/provider validation provided by `run`. Usage reporting is optional through
`UsageTracker`; `NoopCostTracker` is the default, not a billing service.

## Presets as implemented

`short_video` declares brief → scenePlanning → optional voiceover → optional
captions → videoRendering → export. Its default dependency composition currently
fails because scenePlanning requires scriptGeneration, absent from that sequence.
Do not solve this by ignoring dependency errors or silently substituting fake
module definitions; the first roadmap task fixes the production composition.

`long_form_script_voiceover` declares brief → optional research → optional dossier
→ outline → scriptGeneration → postProcessing → qa → optional voiceover → export.
Rendering is absent. Disabling research while enabling dossier is invalid because
dossier requires research. The preset defaults to English and narration tempo
0.92, with localization handled at the export boundary.

## Approval and persistence boundaries

Script, scene-plan and final-export review behavior exists in modules and the
domain approval state machine. Rejection and requested changes preserve artifacts
and history. Seeded results plus resolved checkpoints let the engine continue
without rerunning completed work. Failed seeded results stop execution rather
than acting as a general restart scheduler.

The engine is synchronous and returns results; it does not own a durable run/job
repository or background queue. HTTP start/resume routes currently update an
in-memory record without invoking it. API tests and direct engine tests must not
be mistaken for proof that those layers are connected. The roadmap adds that
connection, durable state and eventually background execution.

## Useful offline evidence

Run `python -m pytest backend/tests/integration/test_long_form_workflow.py` to
exercise real module composition with fake providers and temporary artifact
storage. The unit engine/registry/approval/retry tests validate failures and
continuation. TTS acceptance tests `test_t064.py`, `test_t078.py` and integration
`test_t089.py` exercise resumable narration and preview/production identity.

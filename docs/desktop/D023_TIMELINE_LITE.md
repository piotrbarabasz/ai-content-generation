# D023 — Timeline Lite

## Implementation

The editor has a docked timeline with one paired visual/narration layer. Each row
shows the scene, pinned audio artifact and variant, exact rational offset and
duration in seconds, half-open sample range and timing quality. Refresh lists
eligible retained, selected scene inputs. Add selects an explicit input; remove
selects a subset (at least one clip must remain). Earlier/later moves adjust both
layers together. There are no gaps, overlaps or independent audio drags.

`TimelineEditingService` owns append, remove, reorder and source-range commands.
It rebuilds cumulative offsets with D018's exact arithmetic and frame rounding.
Editing an existing clip never resolves a newer active image or audio selection.
Only adding a clip resolves current selections. Refresh does not replace pinned
media in the persisted timeline. An event token rejects stale commands, including
an A → B → A sequence whose final content equals its initial content.

`ProjectTimelineMedia.boundaries` reads the retained source WAV and D012 map,
verifies source identity and permits measured sentence edges inside the original
D013 scene span. Technical TTS chunk edges are not sentence cuts. Approximate
tempo maps (or missing maps) expose only the existing scene endpoints, permitting
no new internal cut. A trimmed clip can be restored to its original scene span.

`ProjectTimelineEdits` stores immutable parent-linked JSON snapshots using D004's
configured artifact storage and SQLite index, on the project's owning thread.
It validates checksums, project ownership, snapshot identity and an unbranched
history. Invalid bounds or stale commands publish nothing. A committed snapshot
is reconciled if publication reports a subsequent staging-cleanup error. No new
schema, providers, model execution, audio files or render jobs are introduced.

The entrypoint composes the timeline for local projects. `ProjectEditor` also
accepts an injected factory, preserving UI/core separation and test isolation.
Scene and audio preparation retain their existing D021/D022 composition contracts.

## Acceptance evidence

- Displayed order, offsets and durations match persisted snapshots: Qt commands
  operate over actual SQLite/files and rebind to the saved timeline. Project
  reopen verifies the same snapshot independently.
- Invalid bounds preserve history: tests reject mid-sentence technical-chunk
  cuts, arbitrary samples, reversed/empty ranges and out-of-range moves. Measured
  sentence trimming and restoration round-trip successfully.
- Clip moves preserve intended audio: changed image selection does not replace
  pinned media; mixed-rate reorder retains exact audio identity and offsets.
  Existing artifact bytes and provider call counts are unchanged.
- Additional checks cover stale commands, A → B → A tokens, duplicate scenes,
  subset removal, corrupt history, failed publication and post-commit cleanup.

Validation results are recorded in the D023 implementation-plan entry.

## Limits and review

D021 still has its separately recorded real managed-runtime/audible-playback
smoke outstanding. D023 consumes its retained audio contracts; it does not close
that gate. Qt validation is automated and offscreen; no manual packaged-app
visual or listening smoke is claimed. Review the dock layout on the target screen
before release. Candidate discovery and verification currently run synchronously
on the owning GUI thread; large retained histories may require later optimization.
Whole-film proxy playback, rendering actions, multitrack editing, arbitrary cuts,
keyframes and D024 orchestration remain outside this task.

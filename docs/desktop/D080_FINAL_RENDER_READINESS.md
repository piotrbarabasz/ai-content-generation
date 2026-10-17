# D080: final rendering from retained timelines

Branch: `fix/final-render-readiness`, based on `22fdd47`. This is the explicitly
requested final-render bug fix, not implementation of the earlier general audit.

## Confirmed cause

`compose_regeneration` unconditionally read `audio.production`. D078's primary
`CloudAudioServices` does not expose that legacy port. The exception escaped
composition into the editor's general status handler, so the regeneration output
list never contained `project:video_render`. Button availability depended on that
list. Reopening reran the same incompatible composition; a ready timeline and a
working proxy could not fix it.

There was also an independent availability defect: the final renderer was created
only when a preview service existed. Proxy preview is not a prerequisite of the
existing final renderer. These causes are reproduced with actual cloud audio,
temporary SQLite projects and the real button, without contacting providers.

## Change boundary

- Final renderer composition discovers FFmpeg/ffprobe independently. Missing tools,
  missing files and initialization exceptions have visible Export explanations.
- The legacy audio-generation port is optional. Retained narration remains usable;
  cloud paid generation stays on its separate approved path.
- The main Export button uses a render-only action over the saved timeline. It
  never traverses narration/prompt/image/timing/timeline generation steps.
- Readiness checks saved selected media in a background thread. Frozen read inputs
  carry values and artifact paths, never SQLite connections. Project/revision
  tokens discard superseded checks. A refresh action rechecks tool/service state.
- Saved timeline changes, project open, Automatic completion, render start/end and
  explicit refresh update the controls. A missing/stale timeline is not enabled.
- The existing renderer, process ownership, job retry/cancel rules and D040
  publication gate are reused. Verified current final results are reused.
- Final staging performs copy/hash/image preparation in a worker with cooperative
  cancellation between clips. Failed staging remains retained. The final owner
  gate still checks current selections and actual bytes, using one fresh catalog
  snapshot instead of repeatedly querying the catalog for each nested lookup.
- Export exposes readiness, progress, cancel and the last outcome outside
  Advanced. Playback/copy/Delivery/Publishing/Shorts continue resolving the existing
  selected, verified original-render artifact, not a proxy or arbitrary file.

Publication/selection transactions deliberately remain on their owner thread.
This bounded fix is not a complete solution to all main-thread costs identified
in the performance audit; broad refresh/diagnostic and publication performance
remain separate work.

## Validation

All tests use temporary projects and synthetic media. Provider tripwires reject
OpenAI speech, structured text, image generation, and asset-regeneration calls.
The short real-FFmpeg UI smoke uses synthetic retained FHD motion masters and
publishes a 1920×1080 MP4 through normal encoding, ffprobe, full decode and D040
registration. It checks playback/copy callbacks, byte-identical saving, downstream
original-render resolvers, reuse without another job, reopening and unchanged
prior script/audio/image/timeline/proxy bytes.

Cancellation holds a staging worker at a test barrier while Qt continues dispatching
timer events, then cancels through the visible button. Existing render tests cover
publication failures, corruption, stale input changes, historical snapshots and
restart behavior. No real 64-scene export, production project write, paid call,
model/GPU inference or cache cleanup is part of validation.

Focused verification:

- Existing regeneration panel, video render and render-contract tests: 35 passed.
- Cancellation, short synthetic UI render and video-render regressions: 31 passed.
- Real FHD button-to-publication/reuse/reopen smoke: 1 passed in 86.29 s.
- All nine new readiness cases passed in the full backend run, including the FHD
  smoke, missing/stale prerequisites, restart, failure and cancellation.

The full run also found a pre-existing failure in
`test_duration_repair.py::test_d078_cloud_measurements_drive_duration_repair_without_images`:
`Section synthesis incomplete; valid chunks remain available for retry.` The same
test fails with the same error in an untouched `git archive` of starting commit
`22fdd478d465bc0bd53193b97f110dbed6e9c9c4`, tested outside the working tree with
the same interpreter. No narration code/test was changed to conceal that failure.
Baseline evidence is retained in the OS temporary directory under
`aics-d080-baseline-pi0vodgy/baseline-result.txt`.

Full check using the existing isolated interpreter:

```text
.\.venv\Scripts\python.exe -m pytest backend/tests
1 failed, 2552 passed, 12 skipped in 1491.70s (0:24:51)
```

The single failure is the baseline narration test above; all nine new readiness
tests pass. Focused counts overlap and must not be added to the full-suite count.
`git diff --check`, new-file whitespace and D080 task/link checks pass.
The original Soviet Moon project remains an operator acceptance check, not an
automated-test target.

## Verify the existing Soviet Moon project

1. Close the old application. From this branch and the existing isolated environment,
   launch `.\.venv\Scripts\python.exe run_app.py`. Existing optional runtime startup
   verification can still be slow; it is outside this fix.
2. Open **What If the Soviet Union Landed on the Moon First?** normally. Do not run
   Automatic, generate new audio/images, change motion settings, or rebuild its
   timeline. Confirm the saved timeline still has 64 selected clips.
3. Open **Export**. Wait for the saved-media check to complete. The button should
   show an enabled **Build / rebuild final render** and a ready explanation. If
   tools were corrected while the app was open, choose **Refresh render
   availability**. Missing tools/service/stale-media errors appear here without
   opening Advanced; resolve the stated prerequisite rather than regenerating assets.
4. Click **Build / rebuild final render** once. This starts only local final
   rendering or reuses an already verified matching result. Observe staging,
   encoding/probing/decoding progress. **Cancel final render** waits for cleanup
   and preserves retained sources/staging/history.
5. After successful publication, verify **Final video ready**, **Play final video**
   and **Save copy as…**. Play it and save an MP4 copy. Failure is shown as a failed
   outcome; it does not manufacture a READY artifact.
6. Delivery should resolve the verified original. Quality and Shorts can use that
   same original, subject to their existing Standard-plan/timing and review gates.
   Do not start new inference, a paid review or a large Shorts batch just to verify
   source availability.

The 627.8-second narration reported by the user is within the stated 540–660-second
Standard range. This fix does not alter that measurement or weaken duration/media
validation. Successful playback/encoding of the production movie must be reported
by the operator after these steps; it has not been claimed from synthetic tests.

# D002: Windows desktop packaging spike

Task branch: `spike/d002-desktop-packaging`, based on D001 commit `7d25e8d`.
This is a packaging experiment, not the editor, durable job protocol, installer
or an AI runtime. The architecture in ADR 0003 remains unchanged.

## Design

- The direct entry point is `backend/app/desktop/spike.py`. Qt Widgets hosts
  `QMediaPlayer`, `QAudioOutput` and `QVideoWidget`; playback is asynchronous.
- `QProcess` launches the same standalone EXE with `--worker`. In development it
  uses the current interpreter and the entry script. One JSON ping travels over
  stdin, one reply over stdout, then the child exits. Worker startup imports no Qt.
- Worker errors, invalid media and timeouts fail the smoke with a nonzero exit
  code. Closing the window terminates its running child. This one-shot protocol
  is deliberately not the D007 worker supervisor.
- Qt's bundled multimedia plugins and matching DLLs provide playback. The
  developer's FFmpeg CLI generates fixtures only; it is not used by the app.
  CLI packaging for actual rendering belongs to D041.
- `pyside6-deploy` drives Nuitka in **standalone** mode with MSVC 14.3. The console
  remains enabled for this diagnostic spike, preserving worker stdio. The whole
  `.dist` folder is the artifact; copying only the EXE is insufficient.
- PySide6 and Nuitka are pinned in an isolated spike environment. Default backend
  dependencies and provider composition remain unchanged. Only three source
  modules and synthetic fixtures are staged for packaging, not the repository,
  tests, model caches or developer environment.

Qt documents the deployment wrapper and its standalone mode in
[pyside6-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html).
Decoded audio observations use
[QMediaPlayer audio buffer output](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html).
These observations do not prove that a person heard sound or saw the window.

## Reproduce on a Windows build host

Requirements: x64 Python 3.11, VS 2022 C++ build tools (MSVC 14.3, Windows SDK and
`dumpbin`), FFmpeg 8.1.2 with `libx264` and AAC encoding, and an audio output device for
manual observation. Run from the repository root in **x64 Developer PowerShell
for VS 2022**. Network is needed only to provision build dependencies. Nuitka is
allowed to download its build helpers into the local user cache; no AI providers
or models are installed. Target machines do not run these build commands.

```powershell
python -m venv .tmp/d002-env
.\.tmp\d002-env\Scripts\python.exe -m pip install -r packaging/d002/requirements.txt
.\.tmp\d002-env\Scripts\python.exe packaging/d002/generate_fixtures.py --output build/d002/fixtures
.\.tmp\d002-env\Scripts\python.exe backend/app/desktop/spike.py --fixtures build/d002/fixtures
```

Use **Play WAV**, **Play MP4**, **Stop** and **Ping worker**. The fixtures are a
quiet two-second 440 Hz tone and a moving synthetic test pattern with that tone.
The generator writes a manifest with parameters, FFmpeg version and SHA-256
hashes. Generated media may be copied and redistributed without restriction;
there is no third-party recording or reference voice. Encoding bytes may differ
between FFmpeg versions; the manifest identifies each generated pair.

```powershell
.\.tmp\d002-env\Scripts\python.exe backend/app/desktop/spike.py --fixtures build/d002/fixtures --smoke --report .tmp/d002-source-smoke.json
.\.tmp\d002-env\Scripts\python.exe packaging/d002/build.py --output build/d002/package
.\.tmp\d002-env\Scripts\python.exe packaging/d002/smoke.py --bundle 'build/d002/package/D002 packaging spike.dist' --output .tmp/d002-relocated
```

Build and relocation output directories must be new; choose another `--output`
for a rerun. The build stages source in ignored output, generates the deployment
spec there and verifies the resulting EXE exists. It does not edit source or
check in generated binaries. `--dry-run` prints the compiler command after
staging fixtures and config. `--ffmpeg` accepts an explicit encoder path.

The relocation check copies the entire bundle into a path containing spaces and
Polish characters, starts it from an unrelated working directory, removes
Python/Qt environment overrides and restricts child PATH to Windows System32.
It asserts successful ping/exit, invalid-request and EOF exits, decoded WAV/MP4
playback, and a reported nonzero exit for missing fixtures. JSON reports and
media stderr are written to the supplied evidence directory.

The muted smoke requires real decoded audio samples, MP4 video frames, advancing
position, duration and end-of-media. It does not equate loading a URL with playback.
Reports always leave `clean_windows_verified` and
`manual_picture_and_sound_observed` false; external observation must be recorded
separately.

## Clean Windows acceptance procedure

Use a fresh Windows x64 machine/VM with no system Python, Qt, FFmpeg, compiler or
developer checkout. Ensure an audio endpoint is available (including VM audio).
Copy/extract the **whole** `.dist` folder under a writable user path containing
spaces and non-ASCII characters. Use a standard user, not an elevated shell.

1. Record Windows edition/build, machine/VM type, audio device and archive hash.
   Confirm no Python or FFmpeg installation is present; PATH removal alone is
   not this check.
2. Launch `spike.exe`. Confirm the window appears without missing-DLL prompts.
3. Click **Play WAV**: hear the tone and confirm playback finishes. Click
   **Play MP4**: see the moving colored pattern and hear the tone. Exercise Stop
   and replay; report silence, black video, crashes or unexpected console errors.
4. Click **Ping worker**: confirm `Worker PASS`, echo `D002` and exit code evidence.
5. In PowerShell, from the extracted folder run:

   ```powershell
   .\spike.exe --smoke --report "$env:TEMP\d002-clean-smoke.json"
   $LASTEXITCODE
   Get-Content "$env:TEMP\d002-clean-smoke.json"
   '{"op":"ping","value":"D002"}' | .\spike.exe --worker
   $LASTEXITCODE
   ```

6. Both exits must be zero and the media report must show `automated_pass: true`.
   Record manual WAV sound, MP4 picture/sound, launch and child exchange as
   separate PASS/FAIL entries, with date and tester. Close the app and verify no
   spike child remains. Keep the JSON and observations with the tested hash.

Do not mark D002 complete or start dependent UI work before these observations
pass. A clean-machine failure requires diagnosis and recorded evidence, not an
implicit replacement of PySide6 or ADR 0003. Signing, installer lifecycle,
component license/source distribution audit and minimal release size are D041;
this unsigned diagnostic bundle is not a production release.

## Recorded evidence — 2026-09-12

**Outcome: PARTIAL.** Implementation, source smoke, standalone build and
relocated packaged automation pass. Clean Windows and human playback observation
have not been performed, so D002 is not complete and dependent UI work remains
blocked by this acceptance gate.

| Environment/tool | Observed version |
| --- | --- |
| Build and test host | Microsoft Windows 11 Pro x64, 10.0.22631 |
| Python embedded in bundle | CPython 3.11.9 x64 |
| PySide6 / Qt / shiboken6 | 6.11.2 |
| Packaging | pyside6-deploy from 6.11.2, Nuitka 4.1.1, standalone |
| Compiler | VS 2022 17.3.3, MSVC 14.33.31629, cl 19.33.31629.0 |
| Dependency analysis helper | Dependency Walker 2.2 x64, downloaded by Nuitka |
| Fixture encoder (build host only) | FFmpeg 8.1.2-full_build-www.gyan.dev, GCC 16.1.0 |
| Bundled playback FFmpeg | 7.1.5; avcodec-61.dll file version 61.19.101 |
| Selected host audio output | BenQ GW2280 (NVIDIA High Definition Audio) |

Python's `platform.platform()` reports `Windows-10-10.0.22631-SP0`; Windows CIM
identifies the edition as Windows 11 Pro. Both refer to the same tested host.

| Acceptance evidence | Result |
| --- | --- |
| Source media/worker smoke | PASS: both media reach 2000 ms and end-of-media; child echo/exit 0 |
| Standalone build and launch on developer host | PASS |
| Packaged child exchange and normal exit | PASS: Qt child ping; direct EXE ping returns its own PID and exit 0 |
| Packaged rejection and EOF | PASS: error JSON and exit 2 for both |
| Packaged WAV decoding | PASS: 96000 audio frames, 2000 ms, end-of-media |
| Packaged MP4 decoding | PASS: 48 video frames, 96256 decoded AAC audio frames, 2000 ms, end-of-media |
| Missing fixture | PASS: diagnostic JSON, `automated_pass: false`, exit 1 |
| Unicode/spaces, unrelated cwd, System32-only PATH | PASS for the packaged tests above |
| Clean Windows without system Python | NOT RUN; acceptance not satisfied |
| Human observation of WAV sound and MP4 picture/sound | NOT RUN; acceptance not satisfied |
| Focused `python -m pytest backend/tests/unit/test_desktop_spike.py` | PASS: 14 tests |
| Full `python -m pytest backend/tests` | PASS: 563 tests, 37.75 s |
| `git diff --check` and new-file whitespace checks | PASS |

The final artifacts are local and ignored by Git:

- Bundle: `build/d002/package-v3/D002 packaging spike.dist/` (77 files,
  111219762 bytes, approximately 106.1 MiB).
- Transfer archive: `build/d002/D002-windows-x64-spike.zip` (45160411 bytes,
  approximately 43.1 MiB). Extract the entire archive before testing.
- Archive SHA-256: `51c136f7be72f700c7ace05735a3c2da4bbfc8fc5e120ef8a3e82392773b3d5f`.
- EXE SHA-256: `b532fabd3d1828ca0fe907a5d768f1a822a8e6fbd42cc0b7d747f234fb1d474a`.
- Source report: `.tmp/d002-source-final.json`.
- Final compiler log: `.tmp/d002-build-v3.log`.
- Packaged reports: `.tmp/d002-relocated-v3/media.json`, `relocation.json`,
  `missing-fixtures.json` and the associated stderr logs.
- Fixture provenance/hashes: `fixtures/manifest.json` in the bundle.

The successful build used `--output build/d002/package-v3`; failed earlier
attempts remain in separate ignored directories. No generated media, binaries,
developer paths or runtime reports are added to version control.

### Failures and limitations

- The first noninteractive build refused Nuitka's Dependency Walker download.
  The build recipe now permits required build-helper downloads. It also checks
  for the EXE because the wrapper can log an output directory after a failed build.
- An initial worker test assumed the reported PID equaled the venv launcher PID.
  Windows launched the interpreter as a child of that launcher. Source tests now
  assert an independent positive PID; standalone relocation tests additionally
  assert equality with the actual executable process PID.
- The first complete bundle could run `--worker` directly but failed to spawn it
  through Qt. Nuitka's `sys.executable` resolved an unavailable executable inside
  this bundle. Compiled startup now resolves the invoked `sys.argv[0]`; a focused
  regression test and the rebuilt relocated package both pass. See Nuitka's
  [executable-relative path guidance](https://nuitka.net/user-documentation/common-issue-solutions.html).
- The relocation harness initially used a case-sensitive copied environment map
  to look up `SystemRoot`. It now obtains that value from Windows `os.environ`.
- Qt logged unavailable HEVC Media Foundation encoding during codec discovery.
  The H.264/AAC fixture still decoded completely; this spike makes no HEVC claim.
- This developer machine has Python, Visual Studio and other runtimes installed.
  A restricted-PATH run cannot rule out system DLL dependencies. Automated audio
  decoding and video frame delivery cannot establish audible/visible playback.

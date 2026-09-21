# D041 — Windows MVP installer

## Implemented release boundary

The Windows release is a per-user Inno Setup installer over a Nuitka standalone
folder. It does not require or modify system Python, FFmpeg, drivers or PATH.
`pyproject.toml` now keeps the core dependencies separate from `desktop`, `api`
and `dev`; the release environment installs only the application plus `desktop`.

The build is reproducible from explicit inputs:

- CPython 3.11, PySide6 6.11.2 and Nuitka 4.1.1;
- Inno Setup 6.7.3, with its official installer digest pinned in
  `packaging/d041/components.lock.json`;
- the exact FFmpeg 8.1.2 shared binary set listed by size and SHA-256 in that
  lock. Extra, missing or changed files fail before compilation;
- Qt's multimedia backends are included explicitly. The final bundle contains
  both `ffmpegmediaplugin.dll` and `windowsmediaplugin.dll`.

The generated manifest inventories every bundled file and records component
provenance plus the signing outcome. The audit refuses tests, cache directories,
private keys/secrets and common AI weight formats. Piper runtimes and voices are
never part of the base installer.

## Installed paths and composition

The program installs by default under
`%LOCALAPPDATA%\Programs\AI Content Studio`. Mutable product data lives under
`%LOCALAPPDATA%\AI Content Studio` with separate `runtimes`, `models`, `cache`
and `logs` directories. Projects remain user-selected directories. The installer
has no uninstall-delete rule, so uninstall owns only its program files.

A packaged run resolves `media\ffmpeg.exe` and `media\ffprobe.exe` relative to
its executable and never falls back to PATH. Source development retains PATH
lookup. The default product composition checks the approved D008 Piper CPU
profile under the per-user runtime root. If it is already active, D010/D021 use
its private worker and per-user models/cache. If it is absent or invalid, audio
remains unavailable; startup does not install, download or substitute a provider.

## Build and signing

Create an isolated Python 3.11 release environment, install
`packaging/d041/requirements.txt`, then install `.[desktop]`. Supply the exact
FFmpeg shared `bin` directory and a verified Inno Setup compiler:

```powershell
python packaging/d041/build.py `
  --output build/d041/release `
  --ffmpeg-dir C:\release-inputs\ffmpeg-8.1.2\bin `
  --iscc C:\release-tools\Inno-6.7.3\ISCC.exe
```

Unsigned builds are valid test artifacts but the manifest marks them
`release_eligible: false`. A release signer uses a code-signing certificate from
the Windows certificate store; no PFX, password or token enters the repository
or command line:

```powershell
python packaging/d041/build.py `
  --output build/d041/signed-release `
  --ffmpeg-dir C:\release-inputs\ffmpeg-8.1.2\bin `
  --iscc C:\release-tools\Inno-6.7.3\ISCC.exe `
  --signtool 'C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe' `
  --sign-thumbprint <certificate-store-sha1>
```

The application executable, generated uninstaller and final setup are signed;
SignTool verifies the installer with `/pa /all`. Preserve the generated manifest,
build evidence, certificate subject/thumbprint, timestamp result and installer
SHA-256 in the release record. Follow Microsoft SignTool documentation for key
custody and timestamp policy. Third-party notices ship beside the executable;
the FFmpeg notice records the GPL configuration and corresponding-source URL.

## Automated evidence — developer Windows host

The final local artifact is ignored by Git:

- installer: `build/d041/package-v3/installer/AI-Content-Studio-0.1.0-windows-x64-setup.exe`;
- installer size: 100,564,773 bytes; SHA-256:
  `49200be6111ea9e4cdc096cf1b22ac2f0ed55a671c0e0e813126dd058b18ebd7`;
- standalone inventory: 91 files, 387,325,100 bytes;
- lifecycle report: `.tmp/d041-installer-smoke-v4/installer-smoke.json`;
- standalone/app SHA-256 in that report:
  `9b06bd84f96c9a1127b4e08f59638a1c6afae466541e7a6f8a2423d3d2226bfd`.

The installer was installed without elevation to a Unicode/space path. With PATH
restricted to System32, the real GUI became visible and reported its app-local
FFmpeg/ffprobe; both binaries executed and identified the pinned 8.1.2 build.
Uninstall removed the executable while a project sentinel outside `{app}` stayed
byte-identical. The audited bundle contained no API/test package, AI weights,
model cache or secrets. The artifact is intentionally unsigned and therefore not
release-eligible.

Focused release/desktop/media/runtime tests passed 87/87. The full backend suite
passed 1527 tests with 11 existing optional platform tests skipped in 383.54
seconds. `compileall`, `pip check` and `git diff --check` passed.

## Remaining external release gates

D041 remains **Partial**. Repeat `packaging/d041/installer_smoke.py` on a clean
Windows x64 VM as a standard user with `--clean-windows`, no system Python/Qt/
FFmpeg and a real audio endpoint. Manually verify WAV sound and MP4 picture/sound,
exercise an already provisioned D008 private worker and record that uninstall
preserves an actual project. Then create a signed build and retain successful
signature/timestamp verification. These observations also close the inherited
D002/D008 clean-machine gates; a developer-host restricted-PATH run cannot.

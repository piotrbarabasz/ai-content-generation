# D025 — Installable MVP acceptance evidence

Status: **Partial** (2026-09-21). The automated 16-step acceptance evidence passes
on the development Windows host. Release acceptance remains open until the exact
signed installer passes the recorded human-operated clean-Windows procedure.

## Boundary

D025 adds no product behavior. `packaging/d025/acceptance.py` assembles existing
D024/D041 and integration evidence, rejects incomplete or inconsistent inputs, and
emits a machine-readable release report. A partial report is useful evidence and
returns success by default; `--require-release-pass` returns exit code 2 unless all
external release gates are closed.

The manual report is deliberately bound to the tested installer SHA-256. Every
manual check must be `pass`, its machine/test metadata must be populated, and the
D041 signing evidence must identify that installer as signed and release-eligible.

## Recorded evidence

| Evidence | Result |
| --- | --- |
| Existing behavioral cases covering all 16 MVP steps | PASS — 13 selected cases |
| Fresh managed Piper provisioning and synthesis | PASS — `pl_PL-gosia-medium`, requested `pl`, catalog `pl_PL` |
| Real WAV | PASS — 22,050 Hz, 14.837551 s, worker exit 0, reopen and cancel/retry reuse PASS |
| Actual FFmpeg proxy/render decode | PASS — H.264/AAC, 1.52 s, 38 packaged-Qt frames |
| Installer lifecycle and project retention | PASS on the development host |
| Installer | 100,564,773 bytes; SHA-256 `49200be6111ea9e4cdc096cf1b22ac2f0ed55a671c0e0e813126dd058b18ebd7` |
| Managed runtime/model downloads | 135,359,806 / 63,210,573 bytes |
| Evidence host/profile | Windows 10 build 22631, AMD64, harness Python 3.11.9; runtime profile `4b4ac9e5c0f6f8d650e66b41ec69580e45736cebead2ea42e8e421eb00fbede0` |

The 16 automated steps are component/integration evidence, not a claim that one
person completed the uninterrupted workflow on a clean machine. The actual release
gate still requires all of the following on clean Windows, without system Python or
FFmpeg:

1. Install and launch the signed package.
2. Complete steps 1–16 in the MVP definition with the provisioned private worker.
3. Listen to the generated WAV and confirm the selected Polish voice is audible.
4. Play the final MP4 and confirm both picture and narration.
5. Uninstall and confirm that the user project remains.
6. Record the tester, timestamp, Windows build, machine/VM, audio device, observations,
   and the exact installer SHA-256 in a copy of `manual-report.example.json`.

## Reproduction

Run the selected behavioral cases with JUnit output, the D010 real-Piper smoke, and
the D046 packaged preview smoke. Then assemble their evidence:

```powershell
.\.venv-ci311\Scripts\python.exe packaging/d025/acceptance.py `
  --installer build/d041/package-v3/installer/AI-Content-Studio-0.1.0-windows-x64-setup.exe `
  --installer-report .tmp/d041-installer-smoke-v4/installer-smoke.json `
  --audio-report .tmp/d025-piper-v1/report.json `
  --preview-report .tmp/d046-packaged-smoke-v2/d046-smoke.json `
  --junit-report .tmp/d025-automated-v1/junit.xml `
  --runtime-cache .tmp/d045 `
  --model-root .tmp/d009-real-smoke-v2/models `
  --output .tmp/d025-automated-v1/report.json
```

For final release acceptance, add `--manual-report <completed-report.json>` and
`--require-release-pass`. The current unsigned development installer and absence of
clean-Windows human playback/private-worker evidence intentionally keep D025 Partial.

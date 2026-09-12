"""Small media/IPC window and observational smoke; no editor or job services."""

import json
import os
from pathlib import Path
import platform
import sys

import PySide6
from PySide6.QtCore import QProcess, QTimer, QUrl, qVersion
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class SpikeWindow(QWidget):
    def __init__(self, fixtures: Path, worker_command: list[str], smoke: bool, report: Path | None):
        super().__init__()
        self.fixtures, self.worker_command, self.smoke, self.report = fixtures, worker_command, smoke, report
        self.results = {}
        self.current = None
        self.finished = False
        self.counts = {"audio_frames": 0, "video_frames": 0, "position_ms": 0}
        self.setWindowTitle("AI Content Studio — D002 packaging spike")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Media playback and isolated worker check"))
        self.video = QVideoWidget(self)
        layout.addWidget(self.video, 1)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.buttons = []
        for label, action in [("Play WAV", lambda: self.play("tone.wav")),
                              ("Play MP4", lambda: self.play("pattern.mp4")),
                              ("Stop", self.stop), ("Ping worker", self.ping)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            controls.addWidget(button)
            self.buttons.append(button)
        self.status = QLabel("Ready. WAV and MP4 contain a quiet synthetic test tone.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.25)
        self.audio.setMuted(smoke)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.audio_buffers = QAudioBufferOutput(self)
        self.player.setAudioBufferOutput(self.audio_buffers)
        self.audio_buffers.audioBufferReceived.connect(self.audio_frame)
        self.video.videoSink().videoFrameChanged.connect(self.video_frame)
        self.player.positionChanged.connect(self.position)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.errorOccurred.connect(lambda _error, message: self.fail(message))
        self.process = QProcess(self)
        self.process.started.connect(self.send_ping)
        self.process.finished.connect(self.worker_finished)
        self.process.errorOccurred.connect(lambda _error: self.fail(self.process.errorString()))
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self.fail("Smoke/worker timed out."))
        if smoke:
            for button in self.buttons:
                button.setEnabled(False)
            self.timeout.start(30000)
            QTimer.singleShot(0, self.ping)

    def stop(self):
        self.current = None
        self.player.stop()
        self.status.setText("Stopped.")

    def play(self, name):
        path = self.fixtures / name
        if not path.is_file():
            self.fail(f"Missing fixture: {name}. Generate fixtures before running the source app.")
            return
        self.stop()
        self.counts = {"audio_frames": 0, "video_frames": 0, "position_ms": 0}
        self.current = name
        self.status.setText(f"Playing {name}")
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def audio_frame(self, buffer):
        if self.current and buffer.isValid():
            self.counts["audio_frames"] += buffer.frameCount()

    def video_frame(self, frame):
        if self.current and frame.isValid():
            self.counts["video_frames"] += 1

    def position(self, position):
        if self.current:
            self.counts["position_ms"] = max(position, self.counts["position_ms"])

    def media_status(self, status):
        if status != QMediaPlayer.MediaStatus.EndOfMedia or not self.current:
            return
        name, self.current = self.current, None
        result = dict(self.counts, duration_ms=self.player.duration(), ended=True)
        result["ok"] = (result["audio_frames"] > 0 and result["position_ms"] >= 1000
                        and result["duration_ms"] >= 1000
                        and (name != "pattern.mp4" or result["video_frames"] > 0))
        self.results[name] = result
        self.status.setText(f"Finished {name}: {json.dumps(result)}")
        if self.smoke:
            if not result["ok"]:
                self.fail(f"No valid decoded playback evidence for {name}.")
            elif name == "tone.wav":
                QTimer.singleShot(0, lambda: self.play("pattern.mp4"))
            else:
                self.finish(True)

    def ping(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.status.setText("Waiting for isolated worker reply...")
        if not self.smoke:
            self.timeout.start(10000)
        self.process.start(self.worker_command[0], self.worker_command[1:])

    def send_ping(self):
        self.process.write(b'{"op":"ping","value":"D002"}\n')
        self.process.closeWriteChannel()

    def worker_finished(self, code, status):
        if self.finished:
            return
        raw = bytes(self.process.readAllStandardOutput())
        try:
            reply = json.loads(raw)
            valid = (code == 0 and status == QProcess.ExitStatus.NormalExit
                     and reply.get("ok") is True and reply.get("echo") == "D002"
                     and isinstance(reply.get("pid"), int) and reply["pid"] != os.getpid())
        except (ValueError, AttributeError):
            valid, reply = False, {"error": "Invalid worker reply"}
        self.results["worker"] = {"ok": valid, "exit_code": code, "reply": reply}
        if not valid:
            self.fail("Worker handshake failed: " + raw.decode("utf-8", errors="replace"))
        elif self.smoke:
            self.play("tone.wav")
        else:
            self.timeout.stop()
            self.status.setText("Worker PASS: " + json.dumps(reply))

    def fail(self, message):
        if self.finished:
            return
        self.status.setText("Error: " + message)
        if self.smoke:
            self.results["error"] = message
            self.finish(False)
        else:
            self.timeout.stop()
            self.current = None
            self.player.stop()
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.kill()

    def finish(self, ok):
        if self.finished:
            return
        self.finished = True
        self.timeout.stop()
        self.player.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        evidence = {
            "automated_pass": ok, "pyside6": PySide6.__version__, "qt": qVersion(),
            "python": platform.python_version(), "os": platform.platform(),
            "audio_device": QMediaDevices.defaultAudioOutput().description(),
            "muted": self.smoke, "manual_picture_and_sound_observed": False,
            "worker_executable": self.worker_command[0],
            "clean_windows_verified": False, "results": self.results,
        }
        try:
            self.report.parent.mkdir(parents=True, exist_ok=True)
            self.report.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Cannot write smoke report: {exc}", file=sys.stderr)
            ok = False
        QApplication.instance().exit(0 if ok else 1)

    def closeEvent(self, event):
        self.player.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        event.accept()


def run(fixtures, worker_command, smoke, report):
    application = QApplication([sys.argv[0]])
    window = SpikeWindow(fixtures, worker_command, smoke, report)
    if not smoke:
        window.show()
    return application.exec()

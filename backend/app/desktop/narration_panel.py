"""OpenAI creative settings and explicit estimated-spend approvals; no provider I/O."""
import asyncio
from dataclasses import asdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from app.domain.narration import MODELS, PACING, PRESETS, STYLES, VOICES, NarrationSettings


class NarrationCostDialog(QDialog):
    def __init__(self, estimate, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Approve paid narration operation")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        text = (f"Model: {estimate['model']}\nSections: {estimate['sections']} · regenerate {estimate['regenerate']} · reuse {estimate['reuse']}\n"
                f"Text: {estimate['characters']} characters · {estimate['chunks']} technical chunks\n"
                f"Estimated speech: {estimate['estimated_seconds']:.1f}s (not measured or guaranteed)\n"
                f"Estimated API cost: ${estimate['low_usd']:.4f}–${estimate['high_usd']:.4f} before retries\n"
                f"Pricing: {estimate['pricing']['version']}\n"
                "Each retry consumes this approval. Lost responses/timeouts may be billed twice.\n"
                "The ceiling controls reserved estimated spend; provider billing may vary.\n"
                "Generated speech is AI-generated. Existing artifacts are retained.")
        label = QLabel(text); label.setWordWrap(True); layout.addWidget(label)
        form = QFormLayout(); layout.addLayout(form)
        self.budget = QDoubleSpinBox(); self.budget.setRange(.001, 1000); self.budget.setDecimals(3)
        self.budget.setPrefix("$ "); self.budget.setValue(max(.05, estimate["high_usd"] * 2.1))
        self.attempts = QSpinBox(); self.attempts.setRange(1, 2); self.attempts.setValue(2)
        form.addRow("Approved estimated-spend ceiling", self.budget)
        form.addRow("Maximum paid attempts per chunk", self.attempts)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Approve paid operation")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)


class NarrationSettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.section = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("OpenAI Narration Settings — AI-generated voices"))
        notice = QLabel("Speech models shown here shut down January 6, 2027. Future Realtime support requires a separate validated adapter.")
        notice.setWordWrap(True); layout.addWidget(notice)
        form = QFormLayout(); layout.addLayout(form)
        self.model, self.voice, self.style, self.preset, self.pacing = (QComboBox() for _ in range(5))
        for control, values in ((self.model, MODELS), (self.voice, VOICES), (self.style, STYLES), (self.preset, PRESETS), (self.pacing, PACING)):
            control.addItems(list(values))
        self.model.addItem("gpt-realtime-2.1-mini — adapter not installed")
        self.model.model().item(self.model.count()-1).setEnabled(False)
        self.speed = QSlider(Qt.Horizontal); self.speed.setRange(5, 80); self.speed.setValue(20)
        self.speed_value = QLabel("1.00x")
        speed_row = QHBoxLayout(); speed_row.addWidget(self.speed); speed_row.addWidget(self.speed_value)
        self.speed.valueChanged.connect(lambda value: self.speed_value.setText(f"{value*.05:.2f}x"))
        self.speed.setToolTip("API range 0.25–4.00x; recommended editing range 0.75–1.25x, steps 0.05x. Does not guarantee duration.")
        self.instructions = QPlainTextEdit(); self.instructions.setMaximumHeight(65)
        self.instructions.setPlaceholderText("Custom tone, emphasis, pronunciation and pacing instructions; up to 600 UTF-8 bytes")
        self.project_budget = QDoubleSpinBox(); self.project_budget.setRange(0, 1000); self.project_budget.setDecimals(3)
        self.project_budget.setSpecialValueText("No project ceiling"); self.project_budget.setPrefix("$ ")
        form.addRow("Model (project default)", self.model); form.addRow("Voice", self.voice); form.addRow("Model speaking speed", speed_row)
        form.addRow("Narration style", self.style); form.addRow("Instruction preset", self.preset)
        form.addRow("Custom instructions", self.instructions); form.addRow("Pacing", self.pacing)
        form.addRow("Optional project estimated-spend ceiling", self.project_budget)
        tempo_notice = QLabel("Model speed affects new synthesis. Tempo post-processing remains a separate audio variant.")
        tempo_notice.setWordWrap(True); layout.addWidget(tempo_notice)
        self.targets = QListWidget(); self.targets.setMaximumHeight(75); self.targets.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.targets)
        actions = QGridLayout(); layout.addLayout(actions)
        for index, (label, callback) in enumerate((("Save project defaults", self.save_project), ("Apply to selected sections", self.save_selected),
                                ("Apply to all sections", self.save_all), ("Migrate explicitly to OpenAI", self.migrate))):
            button = QPushButton(label); button.clicked.connect(callback); actions.addWidget(button, index // 2, index % 2)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.usage = QPlainTextEdit(); self.usage.setReadOnly(True); self.usage.setMaximumHeight(90)
        self.usage.setPlaceholderText("Paid attempt and estimated-spend history")
        layout.addWidget(self.usage)

    def bind(self, services):
        self.services = services
        self.setVisible(services is not None)
        if services:
            self.reload()

    def reload(self):
        settings = self.services.narration.settings(self.section.section_id if self.section else None)
        for control, value in ((self.model, settings.model), (self.voice, settings.voice), (self.style, settings.style),
                               (self.preset, settings.preset), (self.pacing, settings.pacing)):
            control.setCurrentText(value)
        self.speed.setValue(round(settings.speed / .05)); self.instructions.setPlainText(settings.instructions)
        project = self.services.narration.project()
        self.project_budget.setValue((project.get("project_budget") or 0) if project else 0)
        self.targets.clear()
        for section in self.services.session.active_script.sections:
            item = QListWidgetItem(section.title); item.setData(Qt.UserRole, section.section_id); self.targets.addItem(item)
            if self.section and section.section_id == self.section.section_id:
                item.setSelected(True)
        reservations = self.services.narration.history("reservation")
        self.status.setText(("OpenAI replacement enabled." if self.services.narration.migrated() else "Historical narration retained; migration is explicit.") +
            f" {len(reservations)} paid request reservations · ${sum(v['reserved_usd'] for v in reservations):.4f} estimated spend reserved. "
            "Save settings before previewing. Settings changes do not generate audio.")
        usage = self.services.narration.history("usage")
        self.usage.setPlainText("\n".join(
            [f"Attempt {r['attempt']}: ${r['reserved_usd']:.4f} estimated reserve · {r['pricing_version']}" for r in reservations[-8:]] +
            [f"{u['state']}: {u.get('measured_seconds', 0):.2f}s measured {u.get('message', '')}" for u in usage[-8:]]))

    def values(self):
        return NarrationSettings(model=self.model.currentText(), voice=self.voice.currentText(), speed=round(self.speed.value() * .05, 2),
            style=self.style.currentText(), preset=self.preset.currentText(), instructions=self.instructions.toPlainText(), pacing=self.pacing.currentText())

    def _save(self, ids=None, migrate=False):
        try:
            settings = self.values()
            if ids is None:
                self.services.narration.save_project(settings, migrate=migrate, project_budget=self.project_budget.value() or None)
            else:
                if not ids:
                    raise ValueError("Select at least one section.")
                if settings.model != self.services.narration.settings().model:
                    raise ValueError("Save a changed model as a project default first.")
                self.services.narration.save_overrides(ids, {k: v for k, v in asdict(settings).items() if k != "model"})
            self.changed.emit(); self.reload()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def save_project(self): self._save()
    def save_selected(self): self._save([i.data(Qt.UserRole) for i in self.targets.selectedItems()])
    def save_all(self): self._save([s.section_id for s in self.services.session.active_script.sections])

    def migrate(self):
        if QMessageBox.question(self, "Explicit narration migration", "Enable OpenAI for future narration? Original audio/video remain retained. "
                "No paid request starts now; generation requires a separate estimate and budget approval.") == QMessageBox.Yes:
            self._save(migrate=True)


async def approve_estimate_async(audio_panel, estimate):
    if not estimate["regenerate"]:
        return True
    dialog = NarrationCostDialog(estimate, audio_panel)
    future = asyncio.get_running_loop().create_future()
    dialog.finished.connect(lambda code: future.set_result(code) if not future.done() else None)
    dialog.open()
    result = await future
    if result != QDialog.Accepted:
        return False
    audio_panel.services.approve_operation(estimate, dialog.budget.value(), max_attempts=dialog.attempts.value())
    return True

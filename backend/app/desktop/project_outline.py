"""Coordinator-facing project outline for plans and saved script sections."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from app.domain.plan_script import planned_section_identity


class ProjectOutline(QTreeWidget):
    section_activated = Signal(str)
    group_activated = Signal(str)

    SECTION_ROLE = Qt.UserRole + 1
    GROUP_ROLE = Qt.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAccessibleName("Project outline")
        self.currentItemChanged.connect(self._activated)
        self.itemClicked.connect(lambda item, _column: self._activated(item, None))
        self._loading = False

    def populate(self, sections=(), plan=None, diagnostics=(), selected_id=None):
        self._loading = True
        self.clear()
        status_by_section = {}
        for item in diagnostics:
            if item.section_id:
                status_by_section.setdefault(item.section_id, []).append(item)
        actual = {section.section_id: section for section in sections}
        selected = None

        def section_item(title, section_id):
            checks = status_by_section.get(section_id, ())
            state = ("blocked" if any(x.state == "BLOCKED" for x in checks) else
                     "review" if any(x.state in {"REVIEW", "REJECT"} for x in checks) else
                     "ready" if checks and all(x.state in {"OK", "READY"} for x in checks) else "pending")
            marker = {"ready": "✓", "pending": "○", "review": "!", "blocked": "×"}[state]
            item = QTreeWidgetItem([f"{marker}  {title}"])
            item.setData(0, self.SECTION_ROLE, section_id)
            item.setToolTip(0, f"{title} — {state.capitalize()}")
            if section_id == selected_id:
                nonlocal selected
                selected = item
            return item

        if plan is not None:
            for group in plan.groups:
                if plan.format.value == "social":
                    planned = group.sections[0]
                    sid = planned_section_identity(plan.project_id, planned.id)
                    section = actual.get(sid)
                    self.addTopLevelItem(section_item(section.title if section else planned.title, sid))
                    continue
                group_item = QTreeWidgetItem([group.title])
                group_item.setData(0, self.GROUP_ROLE, group.id)
                self.addTopLevelItem(group_item)
                for planned in group.sections:
                    sid = planned_section_identity(plan.project_id, planned.id)
                    section = actual.get(sid)
                    child = section_item(section.title if section else planned.title, sid)
                    group_item.addChild(child)
                    child.setToolTip(0, f"{planned.title} · {planned.target_word_count} target words")
                group_item.setExpanded(plan.format.value == "social")
        else:
            for section in sections:
                self.addTopLevelItem(section_item(section.title, section.section_id))
        if selected is not None:
            self.setCurrentItem(selected)
        self._loading = False

    def select_section(self, section_id):
        if not section_id:
            return False
        for i in range(self.topLevelItemCount()):
            parent = self.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.data(0, self.SECTION_ROLE) == section_id:
                    self.setCurrentItem(child)
                    return True
            if parent.data(0, self.SECTION_ROLE) == section_id:
                self.setCurrentItem(parent)
                return True
        return False

    def _activated(self, current, _previous):
        if self._loading or current is None:
            return
        section_id = current.data(0, self.SECTION_ROLE)
        group_id = current.data(0, self.GROUP_ROLE)
        if section_id:
            self.section_activated.emit(section_id)
        elif group_id:
            self.group_activated.emit(group_id)

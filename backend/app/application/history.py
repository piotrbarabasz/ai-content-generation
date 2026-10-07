"""Explicit restoration of retained choices; no providers or storage I/O here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryChoice:
    kind: str
    identity: str
    scope: str
    title: str
    description: str
    created: str
    source: str
    selected: bool
    expected: str | None
    script_revision_id: str
    freshness: str = "Current provider settings are checked by the pipeline."


class HistoryService:
    def __init__(self, session, records):
        self.session, self.records = session, records

    def choices(self):
        return self.records.choices(self.session.active_script)

    def restore(self, choice):
        active = self.session.active_script
        if active.id != choice.script_revision_id:
            raise ValueError("Script changed; refresh history before restoring.")
        # Resolve again, so caller-edited display values cannot change ownership.
        current = next((item for item in self.choices()
                        if (item.kind, item.scope, item.identity) ==
                        (choice.kind, choice.scope, choice.identity)), None)
        if current is None or current.expected != choice.expected:
            raise ValueError("Selection changed; refresh history before restoring.")
        if choice.kind == "script":
            self.session.repository.select_script(choice.identity, expected_active_revision_id=active.id)
        elif choice.kind == "section":
            restored = active.select_section_revision(self.session.repository.get_section(choice.identity))
            self.session.save_script(restored, expected_active_revision_id=active.id)
        else:
            self.records.restore(current)

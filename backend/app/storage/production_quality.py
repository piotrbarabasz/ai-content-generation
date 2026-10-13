"""QA-only append-only reports, evidence and decisions; no production head changes."""
from dataclasses import replace
import json

from app.domain.dependencies import canonical_json
from app.domain.production_quality import QAReport
from app.storage.branding import ProjectBranding


class ProjectQuality:
    def __init__(self, store, project_id):
        self.store, self.project_id = store, project_id
        self.records = ProjectBranding(store, project_id)

    def history(self, kind="report"): return self.records.history("quality_" + kind)

    def one(self, identity, kind):
        manifest = next((m for m in self.history(kind) if m.artifact_id == identity), None)
        if manifest is None: raise ValueError("QA artifact is missing or belongs to another project.")
        return manifest

    def bytes(self, manifest):
        self.one(manifest.artifact_id, manifest.artifact_type.removeprefix("quality_"))
        return self.records.verified_bytes(manifest)

    def read(self, manifest): return json.loads(self.bytes(manifest))

    def save(self, kind, value, **metadata):
        return self.store.save_artifact(kind + ".json", canonical_json(value),
            {"artifact_type": "quality_" + kind, "module_name": "desktop_quality", "project_id": self.project_id, **metadata})

    def report(self, identity):
        m = self.one(identity, "report"); report = QAReport.from_payload(self.read(m))
        if report.source["project_id"] != self.project_id or report.input_fingerprint != m.metadata.get("input_fingerprint"):
            raise ValueError("QA report identity differs from its retained manifest.")
        for f in report.findings:
            for identity in f.evidence:
                e = self.one(identity, "evidence"); self.bytes(e)
                if (e.metadata.get("source_id"), e.metadata.get("source_checksum"), e.metadata.get("run_id")) != (
                        report.source["source_id"], report.source["source_checksum"], report.run_id):
                    raise ValueError("QA evidence belongs to another run/source.")
        for row in report.measurements.get("representative_evidence", []):
            e = self.one(row["artifact_id"], "evidence"); self.bytes(e)
            if (e.metadata.get("source_id"), e.metadata.get("source_checksum"), e.metadata.get("run_id"), e.metadata.get("time_seconds")) != (
                    report.source["source_id"], report.source["source_checksum"], report.run_id, row["time_seconds"]):
                raise ValueError("QA representative evidence belongs to another run/source.")
        return m, report

    def cached(self, fingerprint):
        for m in reversed(self.history()):
            if m.metadata.get("input_fingerprint") == fingerprint:
                try:
                    _, report = self.report(m.artifact_id)
                    if not report.canceled and not any(v.get("mandatory") and v["state"] in {"UNAVAILABLE", "SKIPPED", "CANCELED"} for v in report.outcomes):
                        return m, report
                except (ValueError, KeyError, TypeError, OSError): continue
        return None

    def decisions(self, report, *, history=False):
        known = {f.id for f in report.findings}; values = {}; entries = []
        for m in self.history("decision"):
            d = self.read(m)
            if (d["source_id"], d["source_checksum"]) == (report.source["source_id"], report.source["source_checksum"]) and d["finding_id"] in known:
                values[d["finding_id"]] = {"decision_id": m.artifact_id, **d}
                entries.append(values[d["finding_id"]])
        return entries if history else list(values.values())

    def review(self, report_id, finding_id, status, note, *, new_report_id=None):
        _, report = self.report(report_id); finding = next((f for f in report.findings if f.id == finding_id), None)
        if finding is None: raise ValueError("Unknown finding for this report.")
        if finding.severity != "REVIEW" and status not in {"confirmed", "note"}: raise ValueError("Only advisory REVIEW findings can be acknowledged; integrity failures remain blocked.")
        if status not in {"note", "confirmed", "intentional", "resolved"} or type(note) is not str or not note.strip() or len(note) > 2000:
            raise ValueError("Choose a review action and provide a bounded note.")
        if status == "resolved":
            if not new_report_id: raise ValueError("Resolution requires a new completed QA run.")
            new_m, newer = self.report(new_report_id); old_m = self.one(report_id, "report")
            validator = {"composition": "visual", "integrity": "source"}.get(finding.category, finding.category)
            if (new_m.created_at <= old_m.created_at or newer.canceled
                    or (newer.source["source_id"], newer.source["source_checksum"]) != (report.source["source_id"], report.source["source_checksum"])
                    or any(f.rule_id == finding.rule_id for f in newer.findings)
                    or not any(v["validator_id"] == validator and v["state"] == "PASS" for v in newer.outcomes)):
                raise ValueError("New run must execute the relevant validator and demonstrate that the finding is absent.")
        return self.save("decision", {"report_id": report_id, "finding_id": finding_id, "source_id": report.source["source_id"],
            "source_checksum": report.source["source_checksum"], "status": status, "note": note.strip(), "new_report_id": new_report_id})

    def evidence(self, raw, report, time):
        return self.store.save_artifact("evidence.png", raw, {"artifact_type": "quality_evidence", "module_name": "desktop_quality",
            "project_id": self.project_id, "run_id": report.run_id, "source_id": report.source["source_id"],
            "source_checksum": report.source["source_checksum"], "time_seconds": time})

    def rendered(self, report_id, *, stale=False):
        _, report = self.report(report_id); decisions = self.decisions(report)
        value = {**report.to_payload(), "manual_decisions": decisions, "decision_history": self.decisions(report, history=True), "final_state": report.state(decisions, stale=stale)}
        lines = ["# Production QA report", "", "State: **" + value["final_state"] + "**", "",
                 "Project: " + self.project_id, "Source: " + report.source["source_id"], "SHA-256: " + report.source["source_checksum"],
                 "Engine: " + report.engine_version, "Run: " + report.run_id, "Input fingerprint: " + report.input_fingerprint,
                 "", "## Scan configuration", "", "```json", json.dumps(report.configuration, indent=2), "```", "", "## Validators", ""]
        lines.extend(f"- {v['validator_id']} v{v['version']}: {v['state']} - {v.get('reason', '')}" for v in report.outcomes)
        lines += ["", "## Measurements", "", "```json", json.dumps(report.measurements, indent=2, ensure_ascii=False), "```", "", "## Findings", ""]
        for f in report.findings:
            when = "" if f.start is None else f" [{f.start:.3f}-{f.end:.3f}s]"
            lines += [f"### {f.severity}: {f.rule_id}{when}", "", f.explanation, "", "Action: " + f.action,
                      "Actual: " + json.dumps(f.actual, ensure_ascii=False), "Expected: " + json.dumps(f.expected, ensure_ascii=False),
                      "References: " + ", ".join(f.references), "Evidence artifacts: " + ", ".join(f.evidence), ""]
        lines += ["## Manual review decisions and history", "", "```json", json.dumps(value["decision_history"], indent=2, ensure_ascii=False), "```", "",
                  "Technical QA is not a guarantee of editorial quality, factual accuracy or platform acceptance.", ""]
        return value, "\n".join(lines)

    def export(self, report_id, *, stale=False):
        value, markdown = self.rendered(report_id, stale=stale)
        identity = {"report_id": report_id, "decisions": value["manual_decisions"], "state": value["final_state"]}
        json_m = self.save("export_json", value, identity=identity)
        md_m = self.store.save_artifact("quality-report.md", markdown, {"artifact_type": "quality_export_markdown",
            "module_name": "desktop_quality", "project_id": self.project_id, "identity": identity})
        return json_m, md_m

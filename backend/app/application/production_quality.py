"""Provider-independent read-only scans; only QA-owned history is published."""
import asyncio
import time
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from itertools import combinations

from app.domain.dependencies import content_fingerprint
from app.domain.production_quality import ENGINE_VERSION, SCHEMA_VERSION, QAReport, QARunConfiguration
from app.application.quality_validators import (SourceValidator, TechnicalValidator, DurationValidator, CaptionValidator,
    AudioValidator, VisualValidator, PacingValidator, finding)
from app.runtime.media_process import RenderCanceled
from app.storage.paths import contained_path

VALIDATORS = (SourceValidator, TechnicalValidator, DurationValidator, CaptionValidator, AudioValidator, VisualValidator, PacingValidator)
VERSIONS = {v.id: v.version for v in VALIDATORS} | {"probe": "1", "decode": "1", "sampling": "1", "publishing": "1", "semantic": "1", "mapping": "1"}


def source_measurement(context, canceled, timeout=900):
    digest, size = sha256(), 0
    deadline = time.monotonic() + timeout
    try:
        with context.path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                if canceled(): raise RenderCanceled("QA checksum verification canceled.")
                if time.monotonic() > deadline: raise TimeoutError("QA checksum verification deadline exceeded.")
                digest.update(block); size += len(block)
        return {"source_checksum": digest.hexdigest(), "source_size": size}
    except OSError: return {"source_missing": True}


class ProductionQualityService:
    def __init__(self, sources, records, inspector, *, semantic_validator=None):
        self.sources, self.records, self.inspector = sources, records, inspector
        # A generic QAValidator extension is permitted; no multimodal provider is invented.
        self.semantic_validator = semantic_validator
        self.config = QARunConfiguration()
        for manifest in reversed(records.history("configuration")):
            try:
                self.config = QARunConfiguration(**records.read(manifest)); break
            except (ValueError, TypeError, OSError): continue
        self.running = False

    def fingerprint(self, context, config, inspector_identity):
        semantic = None
        if config.semantic and self.semantic_validator is not None:
            semantic = {"validator": self.semantic_validator.id, "version": self.semantic_validator.version,
                        "identity": self.semantic_validator.identity() if hasattr(self.semantic_validator, "identity") else None}
        return content_fingerprint({"schema": SCHEMA_VERSION, "engine": ENGINE_VERSION, "source": context.identity(),
            "configuration": config.to_payload(), "validators": VERSIONS, "inspector": inspector_identity, "semantic": semantic})

    async def run(self, source_id, config=None, *, reuse=True, canceled=lambda: False, progress=lambda *_: None):
        if self.running: raise ValueError("A QA scan is already running.")
        config = config or self.config
        if not isinstance(config, QARunConfiguration): raise ValueError("Expected a QA configuration.")
        context = self.sources.capture(source_id)
        self.config = config; self.records.save("configuration", config.to_payload())
        self.running = True
        outcomes, findings, measurements, sampled = [], [], {}, []
        root = contained_path(self.sources.workspace, "work/quality/" + uuid4().hex); root.mkdir(parents=True)
        run_id = "qa_run_" + uuid4().hex
        try: identity = await asyncio.to_thread(self.inspector.identity) if self.inspector else {"adapter": "unavailable"}
        except Exception:
            self.running = False
            raise
        fingerprint = self.fingerprint(context, config, identity)
        def outcome(name, state="PASS", reason="", mandatory=True):
            outcomes[:] = [v for v in outcomes if v["validator_id"] != name]
            outcomes.append({"validator_id": name, "version": VERSIONS[name], "state": state, "reason": reason, "mandatory": mandatory})
        def validate(cls):
            result = cls().validate(context, config, measurements); findings.extend(result)
            state = "FAIL" if any(f.severity == "BLOCKED" for f in result) else "PASS"
            if cls is DurationValidator and any(f.rule_id == "DURATION_ACCEPTANCE_UNAVAILABLE" for f in result): state = "UNAVAILABLE"
            outcome(cls.id, state)
        async def inspect(name, operation):
            if canceled(): raise RenderCanceled("QA canceled before " + name)
            progress("QA: " + name, len(outcomes), len(VERSIONS))
            try:
                value = await asyncio.wait_for(operation(), config.validator_timeout)
                outcome(name); return value
            except RenderCanceled: raise
            except Exception as exc:
                # Local codec/filter errors contain no credentials; redact the source path.
                reason = str(exc).replace(str(context.path), context.source_id).replace(str(root), "QA workspace")[:2000]
                blocked = name in {"probe", "decode"}
                outcome(name, "FAIL" if blocked else "UNAVAILABLE", reason)
                findings.append(finding(context, "MEDIA_" + name.upper() + "_FAILED" if blocked else "VALIDATOR_" + name.upper() + "_UNAVAILABLE",
                    "BLOCKED" if blocked else "INFO", "technical" if blocked else name,
                    "Local inspection failed: " + reason, "Review the source/tool availability and rerun this validator."))
                return None
        try:
            progress("Verifying source checksum", 0, len(VERSIONS))
            measurements.update(await asyncio.to_thread(source_measurement, context, canceled, config.validator_timeout)); validate(SourceValidator)
            outcome("mapping", "PASS" if context.facts.get("mapping_available", True) else "UNAVAILABLE", context.facts.get("mapping_reason", ""))
            source_ok = not any(f.severity == "BLOCKED" for f in findings)
            if source_ok and reuse:
                cached = self.records.cached(fingerprint)
                if cached:
                    progress("Reused verified QA report", 1, 1); return cached[0]
            if source_ok and self.inspector:
                p = await inspect("probe", lambda: self.inspector.probe(context.path, root, config, canceled=canceled, progress=progress))
                if p:
                    measurements["probe"] = p
                    if config.mode == "deep":
                        decoded = await inspect("decode", lambda: self.inspector.decode(context.path, root, config, canceled=canceled, progress=progress))
                        if decoded: measurements.update(decoded)
                    else: outcome("decode", "NOT_REQUESTED", "Quick QA does not decode the complete video.", False)
                    validate(TechnicalValidator); validate(DurationValidator)
                    if len(p["video_streams"]) == 1:
                        validate(CaptionValidator)
                        if config.mode == "deep" and context.facts.get("captions", {}).get("state") == "valid" and measurements.get("caption_coverage", {}).get("state") == "UNAVAILABLE":
                            outcome("captions", "UNAVAILABLE", "Caption timing checked; independent aligned speech coverage unavailable.")
                    else: outcome("captions", "SKIPPED", "No unambiguous video duration.")
                    for issue in context.facts.get("publishing_findings", []):
                        findings.append(finding(context, issue["rule"], issue["severity"], "publishing", issue["reason"], "Review current publishing assets through Publish.", issue.get("actual")))
                    outcome("publishing", "PASS", mandatory=False)
                    if config.mode == "deep":
                        if len(p["audio_streams"]) == 1:
                            audio = await inspect("audio", lambda: self.inspector.audio(context.path, root, config, canceled=canceled, progress=progress))
                            if audio:
                                measurements["audio"] = audio; findings.extend(AudioValidator().validate(context, config, measurements))
                        else: outcome("audio", "SKIPPED", "No unambiguous audio stream.")
                        if len(p["video_streams"]) == 1:
                            visual = await inspect("visual", lambda: self.inspector.visual(context.path, root, config, canceled=canceled, progress=progress))
                            if visual:
                                measurements["visual"] = visual
                                if context.facts.get("shots"):
                                    frames = await inspect("sampling", lambda: self.inspector.frames(context.path, root, context.facts["shots"], config, canceled=canceled, progress=progress))
                                else:
                                    frames = None; outcome("sampling", "UNAVAILABLE", "No verified shot boundaries: repeated-image analysis unavailable.")
                                sampled = frames or []
                                measurements["sampled_shots"] = [{k: v for k, v in f.items() if k != "raw"} for f in sampled]
                                near = []
                                shots = context.facts.get("shots", [])
                                for first, second in combinations(sampled, 2):
                                    a, b = shots[first["shot_index"]], shots[second["shot_index"]]
                                    distance = (first["hash"] ^ second["hash"]).bit_count()
                                    if a["id"] != b["id"] and a.get("image_checksum") != b.get("image_checksum") and distance <= config.hash_distance:
                                        if len(near) < config.max_intervals:
                                            near.append({"distance": distance, "start": a["start"], "end": b["end"], "references": [a["id"], b["id"]]})
                                        else: outcome("sampling", "UNAVAILABLE", "Similar-shot result limit reached; pairwise evidence is incomplete.")
                                measurements["near_duplicates"] = near
                                findings.extend(VisualValidator().validate(context, config, measurements))
                            if context.facts.get("shots"): validate(PacingValidator)
                            else: outcome("pacing", "UNAVAILABLE", "No verified scene timing; pacing was not guessed.")
                        else:
                            for name in ("visual", "sampling", "pacing"): outcome(name, "SKIPPED", "No unambiguous video stream.")
                        if any(measurements.get(name, {}).get("truncated") for name in ("audio", "visual")):
                            findings.append(finding(context, "INTERVAL_LIMIT_REACHED", "REVIEW", "technical", "Analysis recorded only the configured maximum intervals.", "Increase the bounded interval limit and rerun; this report is incomplete."))
                            outcome("sampling", "UNAVAILABLE", "Some interval evidence was truncated.")
                    else:
                        for name in ("audio", "visual", "sampling", "pacing"): outcome(name, "NOT_REQUESTED", "Deep inspection not requested.", False)
            else:
                for name in VERSIONS.keys() - {"source", "semantic"}: outcome(name, "SKIPPED" if not source_ok else "UNAVAILABLE", "Source integrity failed." if not source_ok else "Trusted FFmpeg/FFprobe unavailable.")
            if config.semantic and source_ok and measurements.get("probe"):
                if self.semantic_validator:
                    result = await inspect("semantic", lambda: asyncio.to_thread(self.semantic_validator.validate, context, config, measurements))
                    if result is not None:
                        if any(f.category != "semantic" or f.severity != "REVIEW" for f in result): raise ValueError("Semantic extension may only emit editorial REVIEW findings.")
                        findings.extend(result)
                else: outcome("semantic", "UNAVAILABLE", "No existing configured multimodal provider contract supports this review; no images/scripts were sent anywhere.")
            else: outcome("semantic", "SKIPPED" if config.semantic else "NOT_REQUESTED", "Source is unverified." if config.semantic else "No semantic/cloud review requested.", config.semantic)
            if canceled(): raise RenderCanceled("QA canceled before report publication.")
            final = await asyncio.to_thread(source_measurement, context, canceled)
            if final != {k: measurements[k] for k in final if k in measurements}:
                findings.append(finding(context, "SOURCE_CHANGED_DURING_QA", "BLOCKED", "integrity", "Source bytes changed during inspection.", "Run QA against a stable verified artifact.", final))
            report = QAReport(run_id, fingerprint, context.identity(), config.to_payload(), tuple(outcomes), measurements, tuple(findings))
            if config.mode == "deep" and config.evidence and source_ok:
                evidence_count = 0; decorated = []
                for f in report.findings:
                    if canceled(): raise RenderCanceled("QA evidence canceled.")
                    if f.start is not None and f.category in {"visual", "composition", "pacing"} and evidence_count < config.max_evidence:
                        time = (f.start + f.end) / 2
                        sample = min(sampled, key=lambda s: abs(s["time"] - time)) if sampled else None
                        if sample is None or not f.start <= sample["time"] <= f.end:
                            try: sample = await self.inspector.frame(context.path, root, time, config, canceled=canceled, index=1000 + evidence_count)
                            except RenderCanceled: raise
                            except Exception:
                                sample = None
                                outcome("sampling", "UNAVAILABLE", "An evidence preview could not be extracted.")
                        if sample:
                            e = self.records.evidence(sample["raw"], report, sample["time"]); evidence_count += 1
                            f = replace(f, evidence=(e.artifact_id,))
                    decorated.append(f)
                report = replace(report, findings=tuple(decorated))
                # Retain a few representative sampled frames even when no visual rule fired.
                # These are measurements, never fabricated findings.
                previews = []
                for sample in sampled[:max(0, config.max_evidence - evidence_count)]:
                    if canceled(): raise RenderCanceled("QA preview retention canceled.")
                    e = self.records.evidence(sample["raw"], report, sample["time"])
                    previews.append({"artifact_id": e.artifact_id, "time_seconds": sample["time"], "shot_index": sample["shot_index"]})
                report = replace(report, measurements={**report.measurements, "representative_evidence": previews})
            after = await asyncio.to_thread(source_measurement, context, canceled, config.validator_timeout)
            if after != final and not any(f.rule_id == "SOURCE_CHANGED_DURING_QA" for f in report.findings):
                report = replace(report, findings=(*report.findings, finding(context, "SOURCE_CHANGED_DURING_QA", "BLOCKED", "integrity", "Source bytes changed during evidence extraction.", "Rerun QA against a stable source.", after)))
            if self.sources.capture(source_id).identity() != context.identity():
                source = {**report.source, "mapping": {**report.source["mapping"], "stale": True}}
                report = replace(report, source=source)
            report = replace(report, outcomes=tuple(outcomes))
            manifest = self.records.save("report", report.to_payload(), input_fingerprint=fingerprint)
            progress("QA report retained: " + report.state(self.records.decisions(report)), 1, 1)
            return manifest
        except RenderCanceled:
            outcome("source", "CANCELED", "Canceled; preceding reports retained.")
            report = QAReport(run_id, fingerprint, context.identity(), config.to_payload(), tuple(outcomes), measurements, tuple(findings), canceled=True)
            self.records.save("report", report.to_payload(), input_fingerprint=fingerprint)
            raise
        finally: self.running = False

    def view(self, report_id):
        _, report = self.records.report(report_id)
        try:
            current = self.sources.capture(report.source["source_id"])
            stale = current.identity() != report.source or self.config.to_payload() != report.configuration or bool(current.facts.get("stale"))
            # File changes also stale an old report even if the immutable manifest is unchanged.
            if current.path.stat().st_size != current.size_bytes: stale = True
            measured = source_measurement(current, lambda: False)
            if measured.get("source_checksum") != current.source_checksum: stale = True
            identity = self.inspector.identity() if self.inspector else {"adapter": "unavailable"}
            if self.fingerprint(current, self.config, identity) != report.input_fingerprint: stale = True
        except (ValueError, OSError, KeyError): stale = True
        return report, self.records.decisions(report), report.state(self.records.decisions(report), stale=stale)

    def current(self, source_id, checksum):
        if self.running: return {"state": "RUNNING", "approved": False, "report_id": None}
        for m in reversed(self.records.history()):
            try:
                _, report = self.records.report(m.artifact_id)
                if not report.canceled and (report.source["source_id"], report.source["source_checksum"]) == (source_id, checksum):
                    report, decisions, state = self.view(m.artifact_id)
                    return {"state": state, "approved": state == "READY", "report_id": m.artifact_id, "report_checksum": m.checksum,
                            "mode": report.configuration["mode"], "input_fingerprint": report.input_fingerprint,
                            "review_findings": [f.id for f in report.findings if f.severity == "REVIEW" and not any(d["finding_id"] == f.id and d["status"] in {"intentional", "resolved"} for d in decisions)],
                            "decisions": decisions}
            except (ValueError, KeyError, TypeError, OSError): continue
        return {"state": "NOT_RUN", "approved": False, "report_id": None}

    def acknowledge_publishing(self, source_id, checksum, note):
        value = self.current(source_id, checksum)
        if value["state"] not in {"REVIEW_REQUIRED", "BLOCKED"} or not value.get("review_findings") or not note.strip(): raise ValueError("Acknowledge an exact current report's REVIEW findings with a note.")
        return self.records.save("publish_ack", {"qa": value, "note": note[:2000]})

    def publishing(self, source_id, checksum):
        value = self.current(source_id, checksum); acknowledged = False
        for m in reversed(self.records.history("publish_ack")):
            if self.records.read(m)["qa"] == value: acknowledged = True; break
        if value.get("review_findings") and value["state"] in {"REVIEW_REQUIRED", "BLOCKED"} and not acknowledged:
            raise ValueError("REVIEW REQUIRED: explicitly acknowledge the current QA review findings in Quality before packaging.")
        return {**value, "review_acknowledged": acknowledged}

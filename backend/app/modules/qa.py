"""Deterministic QA module for long-form script evaluation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256

from app.domain.approval import ApprovalCheckpoint
from app.domain.types import JsonDict
from app.providers.interfaces import LLMProvider
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_DETERMINISTIC_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)
_DEFAULT_THRESHOLD_LIMITS: JsonDict = {
    "minimum_word_count": 0,
    "minimum_outline_sections": 0,
    "minimum_dossier_items": 0,
    "maximum_placeholder_count": 0,
}
_PLACEHOLDER_PATTERNS = (
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bFIXME\b",
    r"\blorem ipsum\b",
    r"\[insert[^\]]*\]",
    r"\{\{[^}]+\}\}",
)


def _pick(mapping: Mapping[str, object], *names: str, default: object | None = None) -> object | None:
    for name in names:
        if name not in mapping:
            continue
        value = mapping[name]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _optional_text(value: object | None) -> str:
    return " ".join(str(value).strip().split()) if value is not None else ""


def _coerce_mapping(value: object | None) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _coerce_text_list(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Iterable[object] = (value,)
    elif isinstance(value, Mapping):
        values = value.values()
    elif isinstance(value, Iterable):
        values = value
    else:
        values = (value,)

    items: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            nested = _coerce_text_list(
                _pick(item, "text", "summary", "title", "heading", "content", "note", "body")
            )
            if nested:
                items.extend(nested)
                continue
        text = _optional_text(item)
        if text:
            items.append(text)
    return items


def _dedupe_texts(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _optional_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _stable_identifier(
    *,
    workflow_run_id: str,
    source_kind: str,
    script_text: str,
    score: int,
) -> str:
    signature = sha256(
        f"qa:{workflow_run_id}:{source_kind}:{score}:{script_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"qa_report_{signature}"


def _extract_script_payload(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> tuple[str, JsonDict, str]:
    explicit_script_value = _pick(inputs, "post_processed_script", "script")
    if explicit_script_value is not None:
        if isinstance(explicit_script_value, Mapping):
            explicit_script = dict(explicit_script_value)
            script_text = _optional_text(
                _pick(explicit_script, "cleaned_text", "text", "script_text", "content", "body")
            )
            if not script_text:
                script_text = _optional_text(_pick(inputs, "script_text", "text"))
            if script_text:
                source_kind = _optional_text(_pick(explicit_script, "source_kind")) or "script"
                return script_text, explicit_script, source_kind
        else:
            script_text = _optional_text(explicit_script_value)
            if script_text:
                return script_text, {"text": script_text}, "script"

    explicit_text = _optional_text(_pick(inputs, "script_text", "text"))
    if explicit_text:
        return explicit_text, {}, "script"

    post_processing_result = module_results.get("postProcessing")
    if post_processing_result is not None:
        output = getattr(post_processing_result, "output", post_processing_result)
        if isinstance(output, Mapping):
            script_text = _optional_text(
                _pick(output, "cleaned_script", "script_text", "text", "content")
            )
            if not script_text:
                post_processed_script = _coerce_mapping(output.get("post_processed_script"))
                script_text = _optional_text(
                    _pick(post_processed_script, "cleaned_text", "text", "script_text")
                )
            if script_text:
                return (
                    script_text,
                    _coerce_mapping(output.get("post_processed_script")),
                    "postProcessing",
                )

    script_generation_result = module_results.get("scriptGeneration")
    if script_generation_result is not None:
        output = getattr(script_generation_result, "output", {})
        if isinstance(output, Mapping):
            script_payload = _coerce_mapping(output.get("script"))
            script_text = _optional_text(
                _pick(
                    script_payload,
                    "text",
                    "script_text",
                    "content",
                    "body",
                )
            )
            if not script_text:
                script_text = _optional_text(_pick(output, "script_text", "text", "content"))
            if script_text:
                return (
                    script_text,
                    script_payload,
                    _optional_text(_pick(output, "source_kind")) or "scriptGeneration",
                )

    raise ValueError("QAModule requires postProcessing output or script text.")


def _extract_context_summary(
    *,
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
    module_name: str,
    summary_keys: tuple[str, ...],
) -> tuple[str, JsonDict]:
    candidate = _pick(inputs, module_name, f"{module_name}_result", f"{module_name}Result")
    if candidate is None:
        candidate_result = module_results.get(module_name)
        if candidate_result is not None:
            candidate = getattr(candidate_result, "output", candidate_result)

    if candidate is None:
        return "", {}

    if isinstance(candidate, Mapping):
        summary: JsonDict = {}
        for key in summary_keys:
            value = candidate.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                items = _dedupe_texts(_coerce_text_list(value))
                if items:
                    summary[key] = items
                continue
            text = _optional_text(value)
            if text:
                summary[key] = text
        if not summary:
            text = _optional_text(candidate)
            if text:
                summary["text"] = text
        return _optional_text(_pick(candidate, *summary_keys)), summary

    text = _optional_text(candidate)
    if not text:
        return "", {}
    return text, {"text": text}


def _count_placeholder_hits(text: str) -> tuple[int, tuple[str, ...]]:
    matches: list[str] = []
    for pattern in _PLACEHOLDER_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            matches.append(pattern)
    return len(matches), tuple(matches)


def _approval_required(inputs: Mapping[str, object]) -> bool:
    explicit_flag = _pick(
        inputs,
        "approval_required",
        "script_approval_required",
        "requires_approval",
    )
    if isinstance(explicit_flag, bool):
        return explicit_flag
    if isinstance(explicit_flag, str):
        return explicit_flag.strip().lower() in {"1", "true", "yes", "y", "required", "pending"}

    approval_policy = _coerce_mapping(_pick(inputs, "approval_policy", "approvalPolicy"))
    for key in ("script", "script_approval", "qa", "post_processing"):
        value = approval_policy.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, str) and value.strip().lower() in {"required", "pending", "review_required"}:
            return True
    return False


def _approval_checkpoint_payload(
    *,
    workflow_run_id: str,
    artifact_id: str,
    recommendation: str,
    next_stage: str,
    checkpoint_type: str,
) -> JsonDict:
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id=workflow_run_id,
        checkpoint_type=checkpoint_type,
        artifact_id=artifact_id,
        required=True,
    )
    return {
        "id": checkpoint.id,
        "workflow_run_id": checkpoint.workflow_run_id,
        "checkpoint_type": checkpoint.checkpoint_type,
        "artifact_id": checkpoint.artifact_id,
        "status": checkpoint.status,
        "required": checkpoint.required,
        "resolved_at": checkpoint.resolved_at.isoformat() if checkpoint.resolved_at else None,
        "decision_history": [asdict(decision) for decision in checkpoint.decision_history],
        "created_at": checkpoint.created_at.isoformat(),
        "approval_recommendation": recommendation,
        "next_stage": next_stage,
    }


class QAModule:
    """Evaluate a long-form script and produce a deterministic QA report."""

    definition = ModuleDefinition(
        name="qa",
        input_schema={
            "type": "object",
            "properties": {
                "post_processed_script": {"type": ["object", "string"]},
                "script": {"type": ["object", "string"]},
                "script_text": {"type": "string"},
                "outline": {"type": ["object", "string"]},
                "dossier": {"type": ["object", "string"]},
                "approval_required": {"type": "boolean"},
                "script_approval_required": {"type": "boolean"},
                "requires_approval": {"type": "boolean"},
                "approval_policy": {"type": "object"},
                "quality_thresholds": {"type": "object"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "qa_report": {"type": "object"},
                "artifact": {"type": "object"},
                "approval_checkpoint": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
                "approval_state": {"type": "string"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "report_name": {"type": "string"},
                "thresholds": {"type": "object"},
                "approval_checkpoint_type": {"type": "string"},
            },
        },
        dependencies=(("postProcessing",),),
        enabled_by_default=True,
        disabled_behavior="fail",
        retry_limit=1,
        artifact_outputs=("qa_report.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        artifact_store: ArtifactStore,
        report_name: str = "qa_report.json",
        thresholds: Mapping[str, object] | None = None,
        approval_checkpoint_type: str = "script",
    ) -> None:
        self._llm_provider = llm_provider
        self._artifact_store = artifact_store
        self._report_name = _optional_text(report_name) or "qa_report.json"
        self._thresholds = {**_DEFAULT_THRESHOLD_LIMITS, **_coerce_mapping(thresholds)}
        self._approval_checkpoint_type = _optional_text(approval_checkpoint_type) or "script"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        script_text, script_payload, source_kind = _extract_script_payload(
            inputs,
            context.module_results,
        )
        outline_text, outline_payload = _extract_context_summary(
            inputs=inputs,
            module_results=context.module_results,
            module_name="outline",
            summary_keys=("title", "topic", "summary", "outline", "sections"),
        )
        dossier_text, dossier_payload = _extract_context_summary(
            inputs=inputs,
            module_results=context.module_results,
            module_name="dossier",
            summary_keys=("title", "topic", "summary", "highlights", "facts", "sections"),
        )

        quality_thresholds = {**self._thresholds, **_coerce_mapping(_pick(inputs, "quality_thresholds", "thresholds"))}
        minimum_word_count = max(0, int(quality_thresholds.get("minimum_word_count", 0) or 0))
        minimum_outline_sections = max(0, int(quality_thresholds.get("minimum_outline_sections", 0) or 0))
        minimum_dossier_items = max(0, int(quality_thresholds.get("minimum_dossier_items", 0) or 0))
        maximum_placeholder_count = max(0, int(quality_thresholds.get("maximum_placeholder_count", 0) or 0))

        script_lines = [line for line in script_text.splitlines() if line.strip()]
        word_count = len(script_text.split())
        placeholder_count, placeholder_patterns = _count_placeholder_hits(script_text)
        outline_sections = len(_coerce_text_list(_pick(outline_payload, "sections", "outline")))
        dossier_items = len(
            _coerce_text_list(
                _pick(dossier_payload, "highlights", "facts", "sections", "summary")
            )
        )

        checks = [
            {
                "name": "script_present",
                "passed": bool(script_text.strip()),
                "details": {"word_count": word_count, "line_count": len(script_lines)},
            },
            {
                "name": "outline_context",
                "passed": outline_sections >= minimum_outline_sections,
                "details": {
                    "outline_sections": outline_sections,
                    "minimum_outline_sections": minimum_outline_sections,
                },
            },
            {
                "name": "dossier_context",
                "passed": dossier_items >= minimum_dossier_items,
                "details": {
                    "dossier_items": dossier_items,
                    "minimum_dossier_items": minimum_dossier_items,
                },
            },
            {
                "name": "placeholder_scan",
                "passed": placeholder_count <= maximum_placeholder_count,
                "details": {
                    "placeholder_count": placeholder_count,
                    "maximum_placeholder_count": maximum_placeholder_count,
                    "matched_patterns": list(placeholder_patterns),
                },
            },
            {
                "name": "length_gate",
                "passed": word_count >= minimum_word_count,
                "details": {
                    "word_count": word_count,
                    "minimum_word_count": minimum_word_count,
                },
            },
        ]

        score = 100
        if not checks[0]["passed"]:
            score = 0
        else:
            if word_count < minimum_word_count:
                score -= min(40, (minimum_word_count - word_count) * 2)
            if placeholder_count > maximum_placeholder_count:
                score -= min(50, (placeholder_count - maximum_placeholder_count) * 20)
            if outline_sections < minimum_outline_sections:
                score -= min(20, (minimum_outline_sections - outline_sections) * 10)
            if dossier_items < minimum_dossier_items:
                score -= min(20, (minimum_dossier_items - dossier_items) * 10)
        score = max(0, min(100, score))

        quality_status = "passed" if all(check["passed"] for check in checks) else "needs_changes"
        approval_recommendation = "approved" if quality_status == "passed" else "changes_requested"
        approval_required = _approval_required(inputs)
        approval_state = "pending" if approval_required else "not_required"
        next_stage = "voiceover" if "voiceover" in context.enabled_modules and "voiceover" not in context.disabled_modules else "export"

        review_reference = self._llm_provider.generate_text(
            (
                "Review the long-form script quality and produce a short deterministic reference "
                f"for {context.workflow_run_id}."
            ),
            context={
                "workflow_run_id": context.workflow_run_id,
                "workflow_config_id": context.workflow_config_id,
                "module_name": self.definition.name,
                "source_kind": source_kind,
                "word_count": word_count,
                "score": score,
                "approval_required": approval_required,
                "approval_recommendation": approval_recommendation,
            },
        )

        qa_report = {
            "qa_report_id": _stable_identifier(
                workflow_run_id=context.workflow_run_id,
                source_kind=source_kind,
                script_text=script_text,
                score=score,
            ),
            "workflow_run_id": context.workflow_run_id,
            "workflow_config_id": context.workflow_config_id,
            "module_name": self.definition.name,
            "source_kind": source_kind,
            "script_text": script_text,
            "script_payload": script_payload,
            "outline": outline_payload,
            "dossier": dossier_payload,
            "quality_thresholds": quality_thresholds,
            "score": score,
            "quality_status": quality_status,
            "approval_recommendation": approval_recommendation,
            "approval_state": approval_state,
            "review_reference": review_reference,
            "checks": checks,
            "next_stage": next_stage,
            "created_at": _DETERMINISTIC_CREATED_AT.isoformat(),
        }

        artifact_manifest = self._artifact_store.save_artifact(
            self._report_name,
            json.dumps(qa_report, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "qa_report",
                "source_kind": source_kind,
                "score": score,
                "approval_recommendation": approval_recommendation,
            },
        )

        approval_checkpoint = None
        if approval_required:
            approval_checkpoint = _approval_checkpoint_payload(
                workflow_run_id=context.workflow_run_id,
                artifact_id=artifact_manifest.storage_key,
                recommendation=approval_recommendation,
                next_stage=next_stage,
                checkpoint_type=self._approval_checkpoint_type,
            )

        output: JsonDict = {
            "qa_report": qa_report,
            "artifact": artifact_manifest.to_payload(),
            "workflow_snapshot": {
                "workflow_run_id": context.workflow_run_id,
                "workflow_config_id": context.workflow_config_id,
                "module_name": context.module_name,
                "enabled_modules": list(context.enabled_modules),
                "disabled_modules": list(context.disabled_modules),
            },
            "source_kind": source_kind,
            "approval_state": approval_state,
            "approval_recommendation": approval_recommendation,
        }
        if approval_checkpoint is not None:
            output["approval_checkpoint"] = approval_checkpoint

        return ModuleResult(
            module_name=self.definition.name,
            status="waiting_for_approval" if approval_required else "completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output=output,
        )


__all__ = ["QAModule"]

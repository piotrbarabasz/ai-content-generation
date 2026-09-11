from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _compared_literals(relative: str, symbol: str) -> set[str]:
    tree = ast.parse(_text(relative))
    matches: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = (node.left, *node.comparators)
        names = {item.id for item in operands if isinstance(item, ast.Name)}
        if symbol not in names:
            continue
        matches.update(
            item.value
            for item in operands
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    return matches


def test_english_first_adr_and_provider_decision_are_indexed() -> None:
    adr = _text("docs/decisions/0002-english-first-localization-boundary.md").lower()
    assert "english is the primary source language" in adr
    assert "export and publishing boundary" in adr
    assert "automatic dubbing is the preferred" in adr
    assert "custom-audio fallback" in adr
    assert "0002-english-first-localization-boundary.md" in _text("docs/INDEX.md")
    provider_decision = _text("docs/tts/M006_PROVIDER_DECISION.md").lower()
    assert "chatterbox multilingual v3 remains the current general production-capable" in provider_decision
    assert "piper remains useful for fast, deterministic local narration" in provider_decision
    assert "xtts-v2 remains evaluation-only" in provider_decision


def test_moss_is_not_registered_in_production_tts_composition() -> None:
    production = "\n".join(
        _text(path).lower()
        for path in (
            "backend/app/providers/tts_factory.py",
            "backend/app/providers/tts_settings.py",
            "backend/app/providers/registry.py",
        )
    )
    assert "moss" not in production


def test_orchestration_has_no_concrete_provider_or_platform_branches() -> None:
    assert "chatterbox" not in _compared_literals(
        "backend/app/workflow/engine.py", "provider"
    )
    assert "youtube" not in _compared_literals(
        "backend/app/workflow/engine.py", "platform"
    )
    assert "chatterbox" not in _compared_literals(
        "backend/app/modules/voiceover.py", "provider"
    )
    voiceover_tree = ast.parse(_text("backend/app/modules/voiceover.py"))
    concrete_imports = {
        alias.name
        for node in ast.walk(voiceover_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any("chatterbox" in name.lower() for name in concrete_imports)

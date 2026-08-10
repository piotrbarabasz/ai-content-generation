from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONCRETE_PROVIDERS = {
    "chatterbox_v3",
    "piper",
    "xtts_v2_eval",
    "moss",
    "moss_tts",
}


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.AST:
    return ast.parse(_source(relative))


def _branch_literals(tree: ast.AST) -> set[str]:
    literals: set[str] = set()
    for node in ast.walk(tree):
        expression: ast.AST | None = None
        if isinstance(node, (ast.If, ast.IfExp)):
            expression = node.test
        elif isinstance(node, ast.Match):
            expression = node.subject
            for case in node.cases:
                literals.update(
                    child.value
                    for child in ast.walk(case.pattern)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                )
        if expression is not None:
            literals.update(
                child.value
                for child in ast.walk(expression)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )
    return literals


def _import_names(tree: ast.AST) -> set[str]:
    return {
        alias.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


def test_engine_and_voiceover_have_no_concrete_provider_selection_branch() -> None:
    for relative in (
        "backend/app/workflow/engine.py",
        "backend/app/modules/voiceover.py",
    ):
        tree = _tree(relative)
        assert _branch_literals(tree).isdisjoint(CONCRETE_PROVIDERS)
        assert not any(
            provider in imported
            for imported in _import_names(tree)
            for provider in CONCRETE_PROVIDERS
        )


def test_production_catalog_registration_is_explicit_and_excludes_experiments() -> None:
    tree = _tree("backend/app/providers/tts_catalog.py")
    registered = [
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TTSCatalogAdapter"
        for keyword in node.keywords
        if keyword.arg == "provider_id"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    ]

    assert registered == ["chatterbox_v3", "piper", "xtts_v2_eval"]
    assert all("moss" not in provider.lower() for provider in registered)
    assert all("experimental" not in provider.lower() for provider in registered)


def test_tts_selection_api_documentation_is_complete_and_indexed() -> None:
    doc = _source("docs/tts/TTS_SELECTION_API.md")
    index = _source("docs/INDEX.md")

    for token in (
        "GET /api/v1/tts/catalog",
        "language",
        "usagePolicy",
        "displayName",
        "supportedLanguages",
        "previewSupported",
        "referenceAudioRequired",
        "POST /api/v1/tts/previews",
        "audioUrl",
        "GET /api/v1/tts/previews/{previewId}/audio",
        "audio/wav",
        "referenceAudioArtifactId",
        "providerConfig",
        "voiceConfig",
        "WorkflowConfig.language",
        "postProcessing.tempo",
        "preview",
        "production",
        "400",
        "404",
        "422",
        "Future UI sequence",
    ):
        assert token in doc
    assert "docs/tts/TTS_SELECTION_API.md" in index

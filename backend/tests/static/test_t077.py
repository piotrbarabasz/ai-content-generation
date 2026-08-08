from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_tts_tooling_imports_no_heavy_chatterbox_dependency_at_module_scope() -> None:
    for relative in (
        "backend/app/providers/chatterbox_v3.py",
        "backend/app/tooling/tts_smoke.py",
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        imports = [
            alias.name
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        assert not any(name.startswith("chatterbox") for name in imports)

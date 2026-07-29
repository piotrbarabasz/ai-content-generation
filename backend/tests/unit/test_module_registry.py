from __future__ import annotations

import pytest

from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry, ModuleRegistryError


def _definition(
    name: str,
    *,
    dependencies: tuple[tuple[str, ...], ...] = (),
    disabled_behavior: str = "fail",
) -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        dependencies=dependencies,
        disabled_behavior=disabled_behavior,
        artifact_outputs=(f"{name}.json",),
    )


def test_module_registry_preserves_registration_order_and_dependency_resolution() -> None:
    registry = ModuleRegistry(
        [
            _definition("brief"),
            _definition("outline", dependencies=(("brief",),)),
            _definition("scriptGeneration", dependencies=(("outline",),)),
            _definition("thumbnail", disabled_behavior="skip"),
        ]
    )

    plan = registry.build_execution_plan(
        ("brief", "outline", "scriptGeneration", "thumbnail"),
        disabled_modules=("thumbnail",),
    )

    assert registry.list_modules() == (
        _definition("brief"),
        _definition("outline", dependencies=(("brief",),)),
        _definition("scriptGeneration", dependencies=(("outline",),)),
        _definition("thumbnail", disabled_behavior="skip"),
    )
    assert plan.module_names == (
        "brief",
        "outline",
        "scriptGeneration",
        "thumbnail",
    )
    assert plan.enabled_modules == ("brief", "outline", "scriptGeneration")
    assert plan.disabled_modules == ("thumbnail",)
    assert plan.step_for("outline").resolved_dependencies == ("brief",)
    assert plan.step_for("scriptGeneration").resolved_dependencies == ("outline",)
    assert plan.step_for("thumbnail").status == "skipped"
    assert plan.step_for("thumbnail").disabled_reason == "disabled"


def test_module_registry_rejects_out_of_order_and_disabled_required_modules() -> None:
    registry = ModuleRegistry(
        [
            _definition("brief"),
            _definition("outline", dependencies=(("brief",),)),
        ]
    )

    with pytest.raises(ModuleRegistryError, match="Unsatisfied module dependency group"):
        registry.build_execution_plan(("outline", "brief"))

    with pytest.raises(ModuleRegistryError, match="Module brief cannot be disabled"):
        registry.build_execution_plan(("brief",), disabled_modules=("brief",))

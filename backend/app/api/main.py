"""FastAPI application entrypoint for the AI Content Studio MVP."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.dependencies import build_api_dependencies, get_api_settings


def create_app() -> FastAPI:
    settings = get_api_settings()
    app = FastAPI(
        title=settings.title,
        version=settings.version,
        description=settings.description,
    )
    app.state.api_settings = settings
    app.state.api_dependencies = build_api_dependencies(settings)
    return app


# Import routes for their router registration side effects after create_app exists.
from app.api.routes import artifacts as _artifacts_routes  # noqa: F401,E402
from app.api.routes import approvals as _approvals_routes  # noqa: F401,E402
from app.api.routes import projects as _projects_routes  # noqa: F401,E402
from app.api.routes import workflow_configs as _workflow_configs_routes  # noqa: F401,E402
from app.api.routes import workflow_runs as _workflow_runs_routes  # noqa: F401,E402


app = create_app()


__all__ = ["app", "create_app"]

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


app = create_app()


__all__ = ["app", "create_app"]

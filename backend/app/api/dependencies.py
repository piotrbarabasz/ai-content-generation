"""Application-level API dependencies and settings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ApiSettings:
    title: str = "AI Content Studio API"
    version: str = "0.1.0"
    description: str = "FastAPI application for the AI Content Studio MVP."
    api_prefix: str = "/api/v1"


@dataclass(slots=True, frozen=True)
class ApiDependencies:
    settings: ApiSettings


def get_api_settings() -> ApiSettings:
    return ApiSettings()


def build_api_dependencies(settings: ApiSettings | None = None) -> ApiDependencies:
    return ApiDependencies(settings=settings or get_api_settings())


__all__ = [
    "ApiDependencies",
    "ApiSettings",
    "build_api_dependencies",
    "get_api_settings",
]

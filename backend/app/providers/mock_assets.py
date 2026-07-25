"""Deterministic mock asset provider."""

from __future__ import annotations

from app.domain.enums import ProviderType

from .interfaces import AssetProvider, _stable_signature, _slugify


class MockAssetProvider(AssetProvider):
    provider_type = ProviderType.ASSET

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def find_assets(self, query: str) -> tuple[dict[str, object], ...]:
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "query": query,
            }
        )
        base_slug = _slugify(query)
        return (
            {
                "provider": self.provider_name,
                "asset_ref": f"mock://asset/{base_slug}/primary",
                "title": f"{query} primary asset",
                "score": 1.0,
                "query_signature": signature[:10],
            },
            {
                "provider": self.provider_name,
                "asset_ref": f"mock://asset/{base_slug}/secondary",
                "title": f"{query} secondary asset",
                "score": 0.5,
                "query_signature": signature[:10],
            },
        )

    def prepare_asset(self, asset_ref: str) -> dict[str, object]:
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "asset_ref": asset_ref,
            }
        )
        return {
            "provider": self.provider_name,
            "asset_ref": asset_ref,
            "prepared_asset_ref": f"{asset_ref}#prepared-{signature[:8]}",
            "status": "prepared",
        }

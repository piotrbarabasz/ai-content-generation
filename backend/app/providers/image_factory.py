"""Explicit image composition; disabled callers never need a concrete provider."""


def build_image_provider(name="mock", *, provider_factories=None):
    if provider_factories is not None and name in provider_factories:
        return provider_factories[name]()
    if name == "mock":
        from .mock_image import MockImageProvider
        return MockImageProvider()
    raise ValueError("Unknown image generation provider.")

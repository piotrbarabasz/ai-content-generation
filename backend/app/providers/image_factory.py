"""Explicit image composition; disabled callers never need a concrete provider."""


def build_image_provider(name="mock", *, settings=None, provider_factories=None,
                         transport=None, environment=None):
    if provider_factories is not None and name in provider_factories:
        return provider_factories[name]()
    if name == "mock":
        from .mock_image import MockImageProvider
        return MockImageProvider()
    if name == "openai":
        from .openai_image import OpenAIImageProvider, OpenAIImageSettings
        return OpenAIImageProvider(OpenAIImageSettings.from_mapping(settings or {}),
                                   transport=transport, environment=environment)
    raise ValueError("Unknown image generation provider.")

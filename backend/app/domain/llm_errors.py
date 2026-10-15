"""Provider-neutral classification for structured response failures."""


class StructuredOutputError(ValueError):
    category = "SECTION_CONTRACT"

    def __init__(self, message, category="SECTION_CONTRACT"):
        super().__init__(message)
        self.category = category


class GeneratedNarrationError(StructuredOutputError):
    def __init__(self, message, category):
        super().__init__(message)
        self.category = category


class LLMRequestCanceled(RuntimeError):
    category = "CANCELLATION"

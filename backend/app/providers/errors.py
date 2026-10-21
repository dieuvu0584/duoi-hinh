"""Typed provider errors. The publisher decides what to do from the class, never from text."""


class ProviderError(Exception):
    """Base class. Messages must be safe to log (no tokens, no raw payloads)."""


class ProviderTimeout(ProviderError):
    """The outcome is UNKNOWN (request may or may not have taken effect). Never retry blindly."""


class ProviderTransient(ProviderError):
    """A retryable failure known NOT to have taken effect (e.g. rate limit, 5xx before work)."""


class ProviderPermanent(ProviderError):
    """A definite rejection; retrying the same request cannot help (bad token, spec error)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class ProviderExpired(ProviderError):
    """The prepared upload/draft expired at the provider; prepare again with a new upload."""

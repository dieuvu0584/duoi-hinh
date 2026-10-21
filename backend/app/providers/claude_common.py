"""Shared plumbing for the Claude adapters: typed error mapping and the messages call."""

from typing import Any

import anthropic

from app.core.logging import redact_text
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient


async def call_messages(client: Any, **kwargs: Any) -> Any:
    """`client.messages.create` with SDK exceptions mapped to our typed provider errors.

    Auth/permission/bad-request/not-found are known to precede billing (Permanent); rate limits
    are Transient; timeouts, connection drops and 5xx may have been processed, so the charge is
    unknown (Timeout).
    """
    try:
        return await client.messages.create(**kwargs)
    except anthropic.RateLimitError:
        raise ProviderTransient("rate limited") from None
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.BadRequestError, anthropic.NotFoundError) as exc:
        raise ProviderPermanent(type(exc).__name__, redact_text(str(exc))[:160]) from None
    except (anthropic.APITimeoutError, anthropic.APIConnectionError,
            anthropic.InternalServerError):
        raise ProviderTimeout("the request may have been processed: charge unknown") from None
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise ProviderTimeout(f"HTTP {exc.status_code}: charge unknown") from None
        raise ProviderPermanent(f"http_{exc.status_code}") from None

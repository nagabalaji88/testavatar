"""Which provider failures are worth retrying, and which are not.

The distinction matters more than it looks. Retrying a 401 wastes the full
backoff ladder before failing anyway; *not* retrying a transient 500 turns a
blip into a dead run. Both mistakes are easy to make by catching one broad
exception class, which is why the classification is explicit here.
"""

from __future__ import annotations

from typing import Optional

from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)


class LLMError(RuntimeError):
    """A provider call that could not be completed."""

    def __init__(self, message: str, *, model: str, retryable: bool) -> None:
        super().__init__(message)
        self.model = model
        self.retryable = retryable


RETRYABLE: tuple[type[Exception], ...] = (
    RateLimitError,
    ServiceUnavailableError,
    APIConnectionError,
    Timeout,
    # InternalServerError subclasses openai.APIError, not litellm's own
    # APIError -- a separate hierarchy, so a `except APIError` that looks
    # exhaustive catches nothing here and a transient 500 escapes as an
    # unhandled exception. Listing it explicitly is the fix.
    InternalServerError,
)

FATAL: tuple[type[Exception], ...] = (
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
)


def classify(exc: BaseException) -> Optional[bool]:
    """True if worth retrying, False if hopeless, None if unrecognised.

    None is deliberately distinct from False: an exception this module has
    never seen should not be silently treated as permanent, and the caller may
    reasonably choose to surface it rather than swallow it.
    """
    if isinstance(exc, FATAL):
        return False
    if isinstance(exc, RETRYABLE):
        return True
    if isinstance(exc, APIError):
        return False
    return None

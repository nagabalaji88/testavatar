"""Trimming provider failures down to what a client may see.

A pipeline failure is stored on the run and served back from the run detail
and log endpoints. The message is not built from a traceback -- it is
`f"{type(exc).__name__}: {exc}"` -- but several provider SDKs put an upstream
traceback inside their own exception text, so the effect is the same: absolute
paths, dependency versions and internal module layout reach the caller.

The server log keeps the whole thing; only the client-reachable copy is cut.
"""

from __future__ import annotations

import re

# Anything that looks like a filesystem path or a traceback frame. Both forms
# appear in provider exception text, and neither tells the caller anything
# actionable about their own run.
_TRACEBACK_LINE = re.compile(r'\s*File "[^"]+", line \d+.*', re.MULTILINE)
_ABS_PATH = re.compile(r"(?:/[\w.\-]+){2,}/[\w.\-]+\.py\b")
_TRACEBACK_HEADER = re.compile(r"Traceback \(most recent call last\):.*", re.DOTALL)

MAX_CLIENT_ERROR_CHARS = 400


def client_safe_error(message: str, *, reveal_internals: bool) -> str:
    """Return `message` reduced to what is safe to hand back to a caller.

    reveal_internals is passed rather than read from settings so the caller
    decides -- local development wants the raw text, and a test wants to pin
    both behaviours without mutating global configuration.
    """
    if reveal_internals:
        return message

    cleaned = _TRACEBACK_HEADER.sub("", message)
    cleaned = _TRACEBACK_LINE.sub("", cleaned)
    cleaned = _ABS_PATH.sub("<path>", cleaned)

    # Collapse the whitespace the removals leave behind, then keep the first
    # sentence-ish span: provider messages lead with the useful part ("rate
    # limit exceeded", "model not found") and trail into internals.
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return "The provider call failed. See the server log for details."
    if len(cleaned) > MAX_CLIENT_ERROR_CHARS:
        cleaned = cleaned[:MAX_CLIENT_ERROR_CHARS].rstrip() + "…"
    return cleaned

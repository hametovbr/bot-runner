"""Hide bot tokens in any text that leaves the runner."""

import re

TOKEN_RE = re.compile(r"(?<![0-9])\d{5,}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")


def mask(text: str) -> str:
    """Replace anything that looks like a bot token with a placeholder."""
    return TOKEN_RE.sub("<token>", text)


def mask_value(value):
    """Mask tokens in strings nested inside lists and dicts."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value

"""Environment variables for handler processes, taken from BOTENV_* variables of the runner.

A variable ``BOTENV_NAME=value`` set on the runner container becomes ``NAME=value`` in
the environment of every handler, background task and program started with
``ctx.shell``. The prefixed originals are removed from that environment.

The variables are read once, when the runner starts; changing them needs a container
restart. Names that would break the worker or the runner's own configuration are
skipped with a warning.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .config import Settings

PREFIX = "BOTENV_"

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Names the worker process depends on.
_RESERVED = {"PATH", "PYTHONPATH", "PYTHONHOME"}
_RESERVED_PREFIXES = ("PYTHON", "LD_")

# uvicorn configures this logger, so the summary shows up in the container log.
log = logging.getLogger("uvicorn.error")


@dataclass
class HandlerEnv:
    """Variables added for handlers, the ones skipped and the full process environment."""

    variables: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    environ: dict[str, str] = field(default_factory=dict)


def _reason_to_skip(name: str) -> str | None:
    if not name:
        return "the name after the prefix is empty"
    if not _NAME_RE.fullmatch(name):
        return "not a valid variable name"
    upper = name.upper()
    if upper in _RESERVED or upper.startswith(_RESERVED_PREFIXES):
        return "reserved for the runner's Python process"
    if upper in {f.upper() for f in Settings.model_fields}:
        return "a runner setting"
    if upper.startswith(PREFIX):
        return "the name still has the prefix"
    return None


def build(environ: Mapping[str, str]) -> HandlerEnv:
    """Collect BOTENV_* variables from ``environ`` and build the handler environment."""
    result = HandlerEnv()
    for key in sorted(environ):
        if not key.startswith(PREFIX):
            continue
        name = key[len(PREFIX) :]
        reason = _reason_to_skip(name)
        if reason:
            result.skipped[key] = reason
        else:
            result.variables[name] = environ[key]
    result.environ = {k: v for k, v in environ.items() if not k.startswith(PREFIX)}
    result.environ.update(result.variables)
    return result


def log_summary(env: HandlerEnv) -> None:
    """Log the names (never the values) of the variables handlers get."""
    for key, reason in env.skipped.items():
        log.warning("Skipping %s: %s", key, reason)
    if env.variables:
        log.info(
            "Handler environment: %d variable(s) from %s*: %s",
            len(env.variables),
            PREFIX,
            ", ".join(env.variables),
        )

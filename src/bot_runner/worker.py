"""Runs one handler call or background task in its own process.

The parent (executor.py) writes a job as JSON to stdin. The worker streams events to
stdout, one per line, each prefixed with MARKER: log lines, Bot API calls and finally
the result. Streaming means the parent keeps the logs even if it has to kill a hanging
handler.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import httpx

from .masking import mask, mask_value
from .sdk import BotApi, Context, Files, _Internal, dumps

MARKER = "\x1e@runner "


class _Events:
    def __init__(self, out: io.TextIOBase):
        self._out = out

    def emit(self, kind: str, data: Any) -> None:
        self._out.write(MARKER + dumps({"type": kind, "data": mask_value(data)}) + "\n")
        self._out.flush()


class _PrintToLog(io.TextIOBase):
    """Turns print() output inside a handler into log lines."""

    def __init__(self, events: _Events):
        self._events = events
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._events.emit("log", line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._events.emit("log", self._buffer)
            self._buffer = ""


def load_module(code: str, name: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": name}
    exec(compile(code, f"<{name}>", "exec"), namespace)  # noqa: S102
    return namespace


async def run_job(job: dict, events: _Events) -> None:
    config = job["config"]
    bot = job["bot"]
    handler = job.get("handler") or {}
    kind = job.get("kind", "handler")
    root = Path(config["files_root"]) / str(bot["id"])
    root.mkdir(parents=True, exist_ok=True)
    shared = config.get("shared_read_dir")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as http:
        api = BotApi(
            http,
            config["bot_api_url"],
            bot["token"],
            events.emit,
            dry_run=job.get("dry_run", False),
        )
        ctx = Context(
            update=job["update"],
            bot={"id": bot["id"], "username": bot.get("username")},
            api=api,
            files=Files(
                root, Path(config["bot_api_data_dir"]), api, Path(shared) if shared else None
            ),
            internal=_Internal(http, config["orchestrator_url"], config["runner_secret"]),
            secrets=job.get("secrets", {}),
            http=http,
            emit=events.emit,
            dry_run=job.get("dry_run", False),
            handler=handler,
            task=job.get("task"),
            code=job["code"],
            origin=job.get("origin") or {},
        )
        module_name = handler.get("name") or "handler"
        namespace = load_module(job["code"], f"{module_name} v{job.get('version', 0)}")
        ctx._namespace = namespace
        if kind == "task":
            entry = job.get("entry", "")
            func = namespace.get(entry)
            if not callable(func):
                raise RuntimeError(f"background task function `{entry}` is not defined")
            result = func(ctx, *job.get("args", []), **job.get("kwargs", {}))
        else:
            func = namespace.get("handle")
            if not callable(func):
                raise RuntimeError("handler code must define a function `handle(update, ctx)`")
            result = func(ctx.update, ctx)
        if inspect.isawaitable(result):
            await result


def main() -> None:
    events = _Events(sys.stdout)
    job = json.loads(sys.stdin.read())
    sys.stdout = _PrintToLog(events)
    try:
        asyncio.run(run_job(job, events))
    except BaseException as exc:  # noqa: BLE001 - report every failure to the parent
        sys.stdout.flush()
        error = "".join(traceback.format_exception(exc, limit=-8))
        events.emit("result", {"status": "error", "error": mask(error)})
        sys.exit(1)
    sys.stdout.flush()
    events.emit("result", {"status": "ok"})


if __name__ == "__main__":
    main()

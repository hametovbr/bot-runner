"""Runs one handler in its own process.

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
from .sdk import KV, BotApi, Context, Files, dumps

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


def load_handler(code: str, version: int):
    namespace: dict[str, Any] = {"__name__": f"handler_v{version}"}
    exec(compile(code, f"<handler v{version}>", "exec"), namespace)  # noqa: S102
    handler = namespace.get("handle")
    if not callable(handler):
        raise RuntimeError("handler code must define a function `handle(update, ctx)`")
    return handler


async def run_job(job: dict, events: _Events) -> None:
    config = job["config"]
    bot = job["bot"]
    root = Path(config["files_root"]) / str(bot["id"])
    root.mkdir(parents=True, exist_ok=True)

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
            files=Files(root, Path(config["bot_api_data_dir"]), api),
            kv=KV(http, config["orchestrator_url"], config["runner_secret"], bot["id"]),
            secrets=job.get("secrets", {}),
            http=http,
            emit=events.emit,
            dry_run=job.get("dry_run", False),
        )
        handler = load_handler(job["code"], job.get("version", 0))
        result = handler(ctx.update, ctx)
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

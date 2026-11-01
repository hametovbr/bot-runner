"""Starts a worker process per handler call or background task and collects its events."""

from __future__ import annotations

import asyncio
import json
import resource
import sys
import time
from typing import Any

from .config import Settings
from .masking import mask
from .worker import MARKER

MAX_LOG_LINES = 500
MAX_API_CALLS = 200
MAX_LINE_LENGTH = 4000
STREAM_LIMIT = 4 * 1024 * 1024
STDERR_TAIL = 8000


def _memory_limit(megabytes: int):
    if megabytes <= 0:
        return None

    def apply() -> None:
        limit = megabytes * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply


class Executor:
    """Runs jobs in subprocesses. Handlers and background tasks have separate limits."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._slots = {
            "handler": asyncio.Semaphore(settings.max_concurrent_runs),
            "task": asyncio.Semaphore(settings.max_concurrent_tasks),
        }
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    async def run(self, job: dict[str, Any]) -> dict[str, Any]:
        kind = "task" if job.get("kind") == "task" else "handler"
        async with self._slots[kind]:
            return await self._run(job, kind)

    def cancel(self, run_id: str) -> bool:
        """Kill a running job. Returns False if no job with this id is running."""
        process = self._running.get(run_id)
        if process is None or process.returncode is not None:
            return False
        self._cancelled.add(run_id)
        process.kill()
        return True

    async def _run(self, job: dict[str, Any], kind: str) -> dict[str, Any]:
        s = self._settings
        limit = s.task_timeout if kind == "task" else s.handler_timeout
        timeout = min(float(job.get("timeout") or limit), limit)
        run_id = job.get("run_id") or ""
        payload = {
            **job,
            "kind": kind,
            "config": {
                "bot_api_url": s.bot_api_url,
                "orchestrator_url": s.orchestrator_url,
                "runner_secret": s.runner_secret,
                "files_root": s.files_root,
                "bot_api_data_dir": s.bot_api_data_dir,
                "shared_read_dir": s.shared_read_dir,
            },
        }
        logs: list[str] = []
        api_calls: list[dict] = []
        result: dict[str, Any] | None = None
        started = time.monotonic()

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bot_runner.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
            preexec_fn=_memory_limit(s.handler_memory_mb),
        )
        if run_id:
            self._running[run_id] = process
        assert process.stdin and process.stdout and process.stderr
        process.stdin.write(json.dumps(payload).encode())
        process.stdin.close()

        async def read_events() -> None:
            nonlocal result
            async for raw in process.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                if not line.startswith(MARKER):
                    # Something wrote to the real stdout (e.g. a C extension); keep it.
                    event = {"type": "log", "data": line}
                else:
                    try:
                        event = json.loads(line[len(MARKER) :])
                    except ValueError:
                        continue
                event_kind, data = event.get("type"), event.get("data")
                if event_kind == "log" and len(logs) < MAX_LOG_LINES:
                    logs.append(mask(str(data))[:MAX_LINE_LENGTH])
                elif event_kind == "api" and len(api_calls) < MAX_API_CALLS:
                    api_calls.append(data)
                elif event_kind == "result":
                    result = data

        async def read_stderr() -> str:
            # Drain stderr completely so the worker never blocks on a full pipe.
            tail = b""
            while chunk := await process.stderr.read(65536):
                tail = (tail + chunk)[-STDERR_TAIL:]
            return tail.decode(errors="replace").strip()

        stderr_task = asyncio.create_task(read_stderr())
        status = "ok"
        error: str | None = None
        try:
            await asyncio.wait_for(read_events(), timeout)
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()
            status, error = "timeout", f"did not finish in {timeout:g} s"
        finally:
            self._running.pop(run_id, None)

        stderr = await stderr_task
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            status, error = "cancelled", "cancelled"
        elif status != "timeout":
            if result is None:
                status = "error"
                error = mask(stderr[-4000:]) or f"worker exited with code {process.returncode}"
                if process.returncode and process.returncode < 0:
                    error = f"worker killed by signal {-process.returncode}. {error}".strip()
            else:
                status = result.get("status", "error")
                error = result.get("error")
        if len(logs) >= MAX_LOG_LINES:
            logs.append(f"... log truncated at {MAX_LOG_LINES} lines")

        return {
            "status": status,
            "error": error,
            "logs": logs,
            "api_calls": api_calls,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

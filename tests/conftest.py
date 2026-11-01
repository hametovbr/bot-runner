"""Test fixtures: a fake Telegram Bot API + orchestrator internal API in a background thread."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response

from bot_runner.config import Settings
from bot_runner.executor import Executor

TOKEN = "123456:" + "A" * 35
SECRET = "test-secret"


class FakeServer:
    def __init__(self, api_data_dir: Path):
        self.calls: list[tuple[str, dict]] = []
        self.kv: dict[str, object] = {}
        self.tasks: dict[int, dict] = {}
        self.progress: list[tuple[int, str]] = []
        self.media = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200_000
        self.api_data_dir = api_data_dir
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/bot{token}/{method}")
        async def bot_api(token: str, method: str, request: Request):
            if token != TOKEN:
                return Response(
                    '{"ok":false,"error_code":401,"description":"Unauthorized"}',
                    status_code=401,
                    media_type="application/json",
                )
            body = await request.json()
            self.calls.append((method, body))
            if method == "getFile":
                path = self.api_data_dir / TOKEN / "documents" / "file_1.pdf"
                return {"ok": True, "result": {"file_id": body["file_id"], "file_path": str(path)}}
            if method in ("sendMessage", "editMessageText"):
                return {
                    "ok": True,
                    "result": {
                        "message_id": body.get("message_id", 1),
                        "text": body["text"],
                        "chat": {"id": body["chat_id"]},
                    },
                }
            if method == "badMethod":
                return {"ok": False, "error_code": 400, "description": "Bad Request: nope"}
            return {"ok": True, "result": True}

        @app.get("/internal/kv/{bot_id}/{key}")
        async def kv_get(bot_id: int, key: str, request: Request):
            assert request.headers["x-runner-secret"] == SECRET
            if key not in self.kv:
                return Response(status_code=404)
            return {"value": self.kv[key]}

        @app.put("/internal/kv/{bot_id}/{key}")
        async def kv_put(bot_id: int, key: str, request: Request):
            self.kv[key] = (await request.json())["value"]
            return {"ok": True}

        @app.delete("/internal/kv/{bot_id}/{key}")
        async def kv_delete(bot_id: int, key: str):
            self.kv.pop(key, None)
            return {"ok": True}

        @app.post("/internal/tasks")
        async def task_create(request: Request):
            assert request.headers["x-runner-secret"] == SECRET
            task_id = len(self.tasks) + 1
            self.tasks[task_id] = {**(await request.json()), "status": "queued"}
            return {"id": task_id}

        @app.get("/internal/tasks/{task_id}")
        async def task_get(task_id: int, bot_id: int):
            task = self.tasks.get(task_id)
            if not task or task["bot_id"] != bot_id:
                return Response(status_code=404)
            return {"id": task_id, "title": task["title"], "status": task["status"]}

        @app.post("/internal/tasks/{task_id}/cancel")
        async def task_cancel(task_id: int):
            task = self.tasks.get(task_id)
            if not task:
                return Response(status_code=404)
            cancelled = task["status"] == "queued"
            task["status"] = "cancelled"
            return {"cancelled": cancelled}

        @app.post("/internal/tasks/{task_id}/progress")
        async def task_progress(task_id: int, request: Request):
            self.progress.append((task_id, (await request.json())["text"]))
            return {"ok": True}

        @app.get("/media/sample.mp4")
        async def media():
            return Response(self.media, media_type="video/mp4")

        return app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake(tmp_path: Path):
    api_dir = tmp_path / "api"
    doc = api_dir / TOKEN / "documents" / "file_1.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"%PDF-1.4 test")
    server = FakeServer(api_dir)
    port = _free_port()
    uv = uvicorn.Server(
        uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("fake server did not start")
        time.sleep(0.05)
    server.url = f"http://127.0.0.1:{port}"
    yield server
    uv.should_exit = True
    thread.join(5)


@pytest.fixture
def settings(fake, tmp_path: Path) -> Settings:
    return Settings(
        runner_secret=SECRET,
        bot_api_url=fake.url,
        orchestrator_url=fake.url,
        files_root=str(tmp_path / "files"),
        bot_api_data_dir=str(fake.api_data_dir),
        handler_timeout=20,
        task_timeout=30,
        handler_memory_mb=0,
        shared_read_dir=str(tmp_path / "shared"),
        packages_dir=str(tmp_path / "packages"),
    )


@pytest.fixture
def executor(settings: Settings) -> Executor:
    return Executor(settings)


def make_job(code: str, **extra) -> dict:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 1761930000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Test"},
            "text": "hello",
            "document": {"file_id": "DOC1", "file_name": "report.pdf"},
        },
    }
    return {
        "bot": {"id": 123456, "username": "test_bot", "token": TOKEN},
        "code": code,
        "version": 1,
        "handler": {"id": 7, "name": "main"},
        "update": update,
        **extra,
    }

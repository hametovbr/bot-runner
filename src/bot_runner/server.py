"""HTTP API used by the orchestrator."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, get_settings
from .executor import Executor


class BotInfo(BaseModel):
    id: int
    username: str | None = None
    token: str


class RunRequest(BaseModel):
    run_id: str | None = None
    bot: BotInfo
    code: str
    version: int = 0
    update: dict[str, Any]
    secrets: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False
    timeout: float | None = None


class RunResult(BaseModel):
    status: str  # ok | error | timeout
    error: str | None = None
    logs: list[str]
    api_calls: list[dict[str, Any]]
    duration_ms: int


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    executor = Executor(settings)
    app = FastAPI(title="bot-runner", version=__version__, docs_url=None, redoc_url=None)

    def check_secret(x_runner_secret: Annotated[str | None, Header()] = None) -> None:
        if not x_runner_secret or not hmac.compare_digest(x_runner_secret, settings.runner_secret):
            raise HTTPException(status_code=401, detail="invalid runner secret")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/run", dependencies=[Depends(check_secret)])
    async def run(request: RunRequest) -> RunResult:
        return RunResult(**await executor.run(request.model_dump()))

    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory bot_runner.server:app`."""
    return create_app()

"""HTTP API used by the orchestrator."""

from __future__ import annotations

import hmac
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__, handler_env
from .config import Settings, get_settings
from .executor import Executor
from .packages import PackageManager


class BotInfo(BaseModel):
    id: int
    username: str | None = None
    token: str


class RunRequest(BaseModel):
    run_id: str | None = None
    #: "handler" calls handle(update, ctx); "task" calls entry(ctx, *args, **kwargs).
    kind: Literal["handler", "task"] = "handler"
    bot: BotInfo
    code: str
    version: int = 0
    #: The handler the code belongs to: id and name (empty for unsaved test code).
    handler: dict[str, Any] = Field(default_factory=dict)
    entry: str = "handle"
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    #: The background task being run: id and title.
    task: dict[str, Any] | None = None
    #: Opaque data the orchestrator wants back in tasks this run starts (e.g. its log row).
    origin: dict[str, Any] = Field(default_factory=dict)
    #: Shared modules importable as shared.<name>: {name: {"version": 3, "code": "..."}}.
    modules: dict[str, dict[str, Any]] = Field(default_factory=dict)
    update: dict[str, Any]
    secrets: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False
    timeout: float | None = None


class PackagesRequest(BaseModel):
    requirements: str


class RunResult(BaseModel):
    status: str  # ok | error | timeout | cancelled
    error: str | None = None
    logs: list[str]
    api_calls: list[dict[str, Any]]
    duration_ms: int


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    packages = PackageManager(settings.packages_dir)
    executor = Executor(settings, packages)
    handler_env.log_summary(executor.handler_env)
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

    @app.get("/packages", dependencies=[Depends(check_secret)])
    async def packages_status() -> dict[str, Any]:
        return packages.status()

    @app.post("/packages", dependencies=[Depends(check_secret)], status_code=202)
    async def packages_install(request: PackagesRequest) -> dict[str, Any]:
        try:
            packages.start(request.requirements)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return packages.status()

    app.state.packages = packages

    @app.post("/cancel/{run_id}", dependencies=[Depends(check_secret)])
    async def cancel(run_id: str) -> dict[str, bool]:
        return {"cancelled": executor.cancel(run_id)}

    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory bot_runner.server:app`."""
    return create_app()

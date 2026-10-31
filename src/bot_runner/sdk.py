"""The `ctx` object handlers receive: Bot API, files, key-value storage, secrets, logs."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .masking import mask, mask_value

Emit = Callable[[str, Any], None]


class Obj:
    """Read-only attribute access over Telegram JSON.

    ``update.message.document.file_name`` works like ``update["message"]["document"]...``;
    a missing field returns ``None`` instead of raising, which matches how optional
    Telegram fields behave.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        # `from` is a Python keyword; allow `message.from_` as an alias.
        key = "from" if name == "from_" else name
        return wrap(self._data.get(key))

    def __getitem__(self, key: str) -> Any:
        return wrap(self._data[key])

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return wrap(self._data.get(key, default))

    def to_dict(self) -> dict:
        return self._data

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        return f"Obj({self._data!r})"


def wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Obj(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value


class TelegramError(Exception):
    def __init__(self, method: str, code: int | None, description: str):
        super().__init__(f"{method} failed: {description}")
        self.method = method
        self.code = code
        self.description = description


def _camel(name: str) -> str:
    """send_message -> sendMessage; sendMessage stays as is."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _encode(value: Any) -> Any:
    if isinstance(value, Path):
        # The local Bot API server accepts local files as file:// URIs.
        return value.resolve().as_uri()
    if isinstance(value, Obj):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    return value


class BotApi:
    """Bot API client bound to one bot.

    ``await ctx.api.send_message(chat_id=..., text=...)`` or
    ``await ctx.api.call("sendMessage", chat_id=..., text=...)``.
    In a dry run only ``get*`` methods reach Telegram; other calls are recorded and
    return ``None``.
    """

    def __init__(
        self, http: httpx.AsyncClient, base_url: str, token: str, emit: Emit, dry_run: bool = False
    ):
        self._http = http
        self._url = f"{base_url.rstrip('/')}/bot{token}/"
        self._emit = emit
        self._dry_run = dry_run

    async def call(self, method: str, **params: Any) -> Any:
        method = _camel(method)
        body = {k: _encode(v) for k, v in params.items() if v is not None}
        record = {"method": method, "params": mask_value(body)}
        if self._dry_run and not method.startswith("get"):
            self._emit("api", {**record, "ok": True, "dry_run": True})
            return None
        try:
            response = await self._http.post(self._url + method, json=body)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._emit("api", {**record, "ok": False, "error": mask(str(exc))})
            raise TelegramError(method, None, mask(str(exc))) from None
        if not data.get("ok"):
            description = data.get("description", "unknown error")
            self._emit("api", {**record, "ok": False, "error": description})
            raise TelegramError(method, data.get("error_code"), description)
        self._emit("api", {**record, "ok": True})
        return wrap(data.get("result"))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        async def method(**params: Any) -> Any:
            return await self.call(name, **params)

        return method


_UNSAFE_NAME = re.compile(r'[\x00-\x1f<>:"|?*\\]')


class Files:
    """File access scoped to the bot's folder on the shared volume.

    Paths are relative to the bot folder. Files downloaded by the local Bot API server
    may be read from its data directory, but writes always land in the bot folder.
    """

    def __init__(self, root: Path, api_data_dir: Path, api: BotApi):
        self.root = root
        self._api_data_dir = api_data_dir
        self._api = api

    def path(self, relative: str | Path = ".") -> Path:
        """Resolve a path inside the bot folder; refuse anything outside it."""
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ValueError(f"path escapes the bot folder: {relative}")
        return target

    @staticmethod
    def safe_name(name: str) -> str:
        """Make a string usable as a file name."""
        cleaned = _UNSAFE_NAME.sub("_", name).replace("/", "_").strip(" .")
        return cleaned or "file"

    def _readable(self, source: str | Path) -> Path:
        source = Path(source)
        resolved = source.resolve() if source.is_absolute() else self.path(source)
        allowed = (self.root.resolve(), self._api_data_dir.resolve())
        if not any(resolved.is_relative_to(base) for base in allowed):
            raise ValueError(f"cannot read outside the bot folder: {source}")
        return resolved

    async def local_path(self, file_id: str) -> Path:
        """Ask the local Bot API server for a file and return its path on disk."""
        info = await self._api.call("getFile", file_id=file_id)
        file_path = Path(info.file_path)
        if not file_path.is_absolute():
            raise RuntimeError(
                "getFile returned a relative path; is the Bot API server running with --local?"
            )
        return file_path

    def _unique(self, target: Path) -> Path:
        if not target.exists():
            return target
        for n in range(1, 10_000):
            candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
            if not candidate.exists():
                return candidate
        raise FileExistsError(target)

    def copy(self, source: str | Path, destination: str | Path, overwrite: bool = False) -> Path:
        src = self._readable(source)
        dst = self.path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite:
            dst = self._unique(dst)
        shutil.copy2(src, dst)
        return dst

    def move(self, source: str | Path, destination: str | Path, overwrite: bool = False) -> Path:
        """Move a file into the bot folder. Returns the final path.

        If the source cannot be removed (for example, the Bot API server owns it), the
        file is copied and the original is left for the server's cache cleanup.
        """
        dst = self.copy(source, destination, overwrite=overwrite)
        try:
            self._readable(source).unlink()
        except OSError:
            pass
        return dst

    def write_bytes(self, destination: str | Path, data: bytes) -> Path:
        dst = self.path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return dst

    def write_text(self, destination: str | Path, text: str) -> Path:
        return self.write_bytes(destination, text.encode())

    def read_bytes(self, source: str | Path) -> bytes:
        return self._readable(source).read_bytes()

    def read_text(self, source: str | Path) -> str:
        return self.read_bytes(source).decode()

    def list(self, relative: str | Path = ".") -> list[Path]:
        folder = self.path(relative)
        return sorted(folder.iterdir()) if folder.is_dir() else []

    def exists(self, relative: str | Path) -> bool:
        return self.path(relative).exists()


class KV:
    """Per-bot key-value storage kept by the orchestrator. Values are JSON."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, secret: str, bot_id: int):
        self._http = http
        self._base = f"{base_url.rstrip('/')}/internal/kv/{bot_id}/"
        self._headers = {"X-Runner-Secret": secret}

    async def get(self, key: str, default: Any = None) -> Any:
        response = await self._http.get(self._base + key, headers=self._headers)
        if response.status_code == 404:
            return default
        response.raise_for_status()
        return response.json()["value"]

    async def set(self, key: str, value: Any) -> None:
        response = await self._http.put(
            self._base + key, headers=self._headers, json={"value": value}
        )
        response.raise_for_status()

    async def delete(self, key: str) -> None:
        response = await self._http.delete(self._base + key, headers=self._headers)
        if response.status_code != 404:
            response.raise_for_status()


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


def chat_id_of(update: dict) -> int | None:
    """Find the chat an update belongs to."""
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "business_message",
    ):
        if key in update:
            return update[key]["chat"]["id"]
    query = update.get("callback_query")
    if query and query.get("message"):
        return query["message"]["chat"]["id"]
    for key in ("my_chat_member", "chat_member", "chat_join_request", "message_reaction"):
        if key in update:
            return update[key]["chat"]["id"]
    return None


class Context:
    """Everything a handler can use. Passed as the second argument to ``handle``."""

    def __init__(
        self,
        *,
        update: dict,
        bot: dict,
        api: BotApi,
        files: Files,
        kv: KV,
        secrets: dict[str, str],
        http: httpx.AsyncClient,
        emit: Emit,
        dry_run: bool,
    ):
        self.update = Obj(update)
        self.bot = Obj(bot)
        self.api = api
        self.files = files
        self.kv = kv
        self.secrets = dict(secrets)
        self.http = http
        self.dry_run = dry_run
        self._emit = emit
        self._chat_id = chat_id_of(update)

    @property
    def chat_id(self) -> int | None:
        return self._chat_id

    def log(self, *values: Any) -> None:
        """Write a line to the update log."""
        self._emit("log", " ".join(str(v) for v in values))

    async def reply(self, text: str, **params: Any) -> Any:
        """Send a message to the chat the update came from."""
        if self._chat_id is None:
            raise RuntimeError("this update has no chat to reply to")
        return await self.api.call("sendMessage", chat_id=self._chat_id, text=text, **params)

    async def answer_callback(self, text: str | None = None, **params: Any) -> Any:
        """Answer the callback query of the current update."""
        query = self.update.callback_query
        if not query:
            raise RuntimeError("this update is not a callback query")
        return await self.api.call(
            "answerCallbackQuery", callback_query_id=query.id, text=text, **params
        )

    async def shell(
        self,
        *args: str | Path,
        timeout: float | None = None,
        cwd: str | Path | None = None,
    ) -> ShellResult:
        """Run an external program (for example ``yt-dlp``) and collect its output.

        ``cwd`` defaults to the bot folder.
        """
        process = await asyncio.create_subprocess_exec(
            *[str(a) for a in args],
            cwd=str(cwd) if cwd else str(self.files.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return ShellResult(
            process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
        )


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

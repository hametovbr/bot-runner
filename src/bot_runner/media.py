"""Downloading media with yt-dlp and sending files back to Telegram."""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .masking import mask

if TYPE_CHECKING:
    from .sdk import Context

MB = 1024 * 1024

VIDEO = {".mp4", ".m4v", ".mov"}
AUDIO = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}
PHOTO = {".jpg", ".jpeg", ".png", ".webp"}
ANIMATION = {".gif"}
PHOTO_LIMIT = 10 * MB

SEND_METHODS = {
    "video": ("sendVideo", "video"),
    "audio": ("sendAudio", "audio"),
    "photo": ("sendPhoto", "photo"),
    "animation": ("sendAnimation", "animation"),
    "voice": ("sendVoice", "voice"),
    "document": ("sendDocument", "document"),
}

DEFAULT_MESSAGES = {
    "start": "⏳ Downloading…",
    "progress": "⏳ Downloading: {percent} of {total} · {speed}",
    "processing": "⚙️ Processing…",
    "done": "✅ Downloaded: {title} ({size})",
    "failed": "❌ Download failed: {error}",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class DownloadFailed(Exception):
    """yt-dlp could not download the URL. The message is safe to show to users."""


@dataclass
class Download:
    path: Path
    title: str
    url: str
    size: int
    #: The status message in the chat, if one was sent.
    message_id: int | None = None
    #: A trimmed copy of yt-dlp's info: id, title, uploader, duration, webpage_url...
    info: dict[str, Any] = field(default_factory=dict)


def human_size(size: float | None) -> str:
    if not size:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO:
        return "video"
    if suffix in AUDIO:
        return "audio"
    if suffix in ANIMATION:
        return "animation"
    if suffix in PHOTO and path.stat().st_size <= PHOTO_LIMIT:
        return "photo"
    return "document"


async def send_file(
    ctx: Context, chat_id: int, path: str | Path, caption: str | None, kind: str, **params: Any
) -> Any:
    target = ctx.files.readable(path)
    if not target.is_file():
        raise FileNotFoundError(path)
    if kind == "auto":
        kind = file_kind(target)
    if kind not in SEND_METHODS:
        raise ValueError(f"unknown kind {kind!r}; use one of {', '.join(SEND_METHODS)} or auto")
    method, field_name = SEND_METHODS[kind]
    if kind == "video":
        params.setdefault("supports_streaming", True)
    return await ctx.api.call(
        method, chat_id=chat_id, caption=caption, **{field_name: target}, **params
    )


_INFO_KEYS = (
    "id",
    "title",
    "uploader",
    "channel",
    "duration",
    "webpage_url",
    "extractor_key",
    "ext",
    "width",
    "height",
    "upload_date",
    "description",
)


def _trim_info(info: dict[str, Any]) -> dict[str, Any]:
    trimmed = {k: info.get(k) for k in _INFO_KEYS if info.get(k) is not None}
    if "description" in trimmed:
        trimmed["description"] = str(trimmed["description"])[:1000]
    return trimmed


def _final_path(info: dict[str, Any]) -> Path | None:
    for item in info.get("requested_downloads") or []:
        if item.get("filepath"):
            return Path(item["filepath"])
    for key in ("filepath", "_filename"):
        if info.get(key):
            return Path(info[key])
    return None


class _QuietLogger:
    """Collects yt-dlp messages into the run log instead of stdout."""

    def __init__(self, ctx: Context):
        self._ctx = ctx

    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            return
        if "File is larger than max-filesize" in message:
            self._ctx.log("yt-dlp:", message)

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self._ctx.log("yt-dlp warning:", _ANSI.sub("", message))

    def error(self, message: str) -> None:
        self._ctx.log("yt-dlp error:", _ANSI.sub("", message))


def _progress_text(state: dict[str, Any], template: str) -> str:
    done = state.get("downloaded_bytes") or 0
    total = state.get("total_bytes") or state.get("total_bytes_estimate")
    percent = f"{done / total * 100:.0f}%" if total else human_size(done)
    speed = state.get("speed")
    eta = state.get("eta")
    return template.format(
        percent=percent,
        downloaded=human_size(done),
        total=human_size(total),
        speed=f"{human_size(speed)}/s" if speed else "?",
        eta=f"{int(eta)} s" if eta is not None else "?",
    )


async def download(
    ctx: Context,
    url: str,
    folder: str = "downloads",
    *,
    audio_only: bool = False,
    format: str | None = None,  # noqa: A002 - mirrors yt-dlp's option name
    max_size_mb: int = 2000,
    cookies: str | Path | None = None,
    filename: str | None = None,
    status: bool = True,
    messages: dict[str, str] | None = None,
    options: dict[str, Any] | None = None,
) -> Download:
    """Download ``url`` with yt-dlp into ``folder`` inside the bot folder.

    - ``audio_only``: extract audio as MP3.
    - ``format``: a yt-dlp format string; by default the best video up to 1080p plus audio.
    - ``max_size_mb``: skip files above this size (Telegram accepts up to 2000 MB here).
    - ``cookies``: a Netscape cookies file, for example ``"/shared/instagram_cookies.txt"``.
    - ``filename``: a yt-dlp output template; default ``"%(title).150B [%(id)s].%(ext)s"``.
    - ``status``: keep a status message in the chat updated with progress.
    - ``messages``: override the status texts (keys: start, progress, processing, done,
      failed; see DEFAULT_MESSAGES for placeholders), for example in another language.
    - ``options``: extra yt-dlp options, merged last.

    Raises ``DownloadFailed`` with a readable message.
    """
    import yt_dlp

    texts = {**DEFAULT_MESSAGES, **(messages or {})}
    target = ctx.files.path(folder)
    target.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {}
    temp_dir = Path(tempfile.mkdtemp(prefix="dl-"))

    def hook(event: dict[str, Any]) -> None:
        state.clear()
        state.update(
            {
                k: event.get(k)
                for k in (
                    "status",
                    "downloaded_bytes",
                    "total_bytes",
                    "total_bytes_estimate",
                    "speed",
                    "eta",
                )
            }
        )

    opts: dict[str, Any] = {
        "outtmpl": filename or "%(title).150B [%(id)s].%(ext)s",
        "paths": {"home": str(target), "temp": str(temp_dir)},
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "max_filesize": max_size_mb * MB,
        "progress_hooks": [hook],
        "logger": _QuietLogger(ctx),
        "merge_output_format": "mp4",
    }
    if audio_only:
        opts["format"] = format or "bestaudio/best"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["format"] = format or "bv*+ba/b"
        opts["format_sort"] = ["res:1080", "ext:mp4:m4a"]
    if cookies:
        # yt-dlp writes cookies back on exit; give it a private copy.
        cookie_copy = temp_dir / "cookies.txt"
        shutil.copyfile(ctx.files.readable(cookies), cookie_copy)
        opts["cookiefile"] = str(cookie_copy)
    opts.update(options or {})

    message_id = None
    if status and ctx.chat_id is not None:
        sent = await ctx.api.call("sendMessage", chat_id=ctx.chat_id, text=texts["start"])
        message_id = sent.message_id if sent else None
    last_text = texts["start"]

    async def show(text: str) -> None:
        nonlocal last_text
        if text == last_text:
            return
        last_text = text
        await ctx.progress(text, force=True)
        if message_id is not None:
            try:
                await ctx.api.call(
                    "editMessageText", chat_id=ctx.chat_id, message_id=message_id, text=text
                )
            except Exception:  # noqa: BLE001 - a failed edit must not stop the download
                pass

    def run() -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=True))

    job = asyncio.create_task(asyncio.to_thread(run))
    try:
        while not job.done():
            await asyncio.wait({job}, timeout=3)
            if job.done():
                break
            if state.get("status") == "downloading":
                await show(_progress_text(state, texts["progress"]))
            elif state.get("status") == "finished":
                await show(texts["processing"])
        try:
            info = job.result()
        except yt_dlp.utils.DownloadError as exc:
            reason = _ANSI.sub("", str(exc)).removeprefix("ERROR: ").strip()
            raise DownloadFailed(mask(reason)[:500]) from None
        path = _final_path(info)
        if path is None or not path.is_file():
            raise DownloadFailed(f"nothing was downloaded (the file may be over {max_size_mb} MB)")
    except DownloadFailed as exc:
        await show(texts["failed"].format(error=exc))
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    size = path.stat().st_size
    title = info.get("title") or path.stem
    await show(texts["done"].format(title=title, size=human_size(size)))
    return Download(
        path=path,
        title=title,
        url=url,
        size=size,
        message_id=message_id,
        info=_trim_info(info),
    )

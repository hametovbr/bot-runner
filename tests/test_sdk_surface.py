"""Snapshot of the public handler SDK.

Handler authors (people and AI agents) learn the SDK from the skill in
bot-orchestrator: skills/bot-orchestrator-handlers. When this test fails, the SDK
changed: update that skill and docs/ARCHITECTURE.md in bot-orchestrator (and the
editor hints in sdk_hints.js), then update the snapshot below. See AGENTS.md.
"""

import dataclasses
import inspect
import re

from bot_runner import media, sdk

MESSAGE = (
    "The public handler SDK changed. Update bot-orchestrator/skills/bot-orchestrator-handlers, "
    "docs/ARCHITECTURE.md and sdk_hints.js, then this snapshot (see AGENTS.md)."
)


def surface(cls: type) -> dict[str, str]:
    """Public methods with their signatures, and public attributes set in __init__."""
    found: dict[str, str] = {}
    for name, member in vars(cls).items():
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            found[name] = "property"
        elif callable(member) or isinstance(member, staticmethod):
            found[name] = str(inspect.signature(getattr(cls, name)))
    init = inspect.getsource(cls.__init__) if "__init__" in vars(cls) else ""
    for name in re.findall(r"self\.([a-z]\w*)(?:: [^=]+)? =", init):
        found.setdefault(name, "attribute")
    return dict(sorted(found.items()))


SNAPSHOT = {
    "Context": {
        "answer_callback": "(self, text: 'str | None' = None, **params: 'Any') -> 'Any'",
        "api": "attribute",
        "background": (
            "(self, func: 'Callable', /, *args: 'Any', title: 'str | None' = None, "
            "**kwargs: 'Any') -> 'int | None'"
        ),
        "bot": "attribute",
        "chat_id": "property",
        "download": "(self, url: 'str', folder: 'str' = 'downloads', **options: 'Any')",
        "dry_run": "attribute",
        "files": "attribute",
        "handler": "attribute",
        "http": "attribute",
        "kv": "attribute",
        "log": "(self, *values: 'Any', sep: 'str' = ' ') -> 'None'",
        "progress": "(self, text: 'str', *, force: 'bool' = False) -> 'None'",
        "reply": "(self, text: 'str', **params: 'Any') -> 'Any'",
        "reply_file": (
            "(self, path: 'str | Path', caption: 'str | None' = None, kind: 'str' = 'auto', "
            "**params: 'Any') -> 'Any'"
        ),
        "secrets": "attribute",
        "shell": (
            "(self, *args: 'str | Path', timeout: 'float | None' = None, "
            "cwd: 'str | Path | None' = None) -> 'ShellResult'"
        ),
        "task": "attribute",
        "tasks": "attribute",
        "update": "attribute",
    },
    "Files": {
        "copy": (
            "(self, source: 'str | Path', destination: 'str | Path', "
            "overwrite: 'bool' = False) -> 'Path'"
        ),
        "exists": "(self, relative: 'str | Path') -> 'bool'",
        "list": "(self, relative: 'str | Path' = '.') -> 'list[Path]'",
        "local_path": "(self, file_id: 'str') -> 'Path'",
        "move": (
            "(self, source: 'str | Path', destination: 'str | Path', "
            "overwrite: 'bool' = False) -> 'Path'"
        ),
        "path": "(self, relative: 'str | Path' = '.') -> 'Path'",
        "read_bytes": "(self, source: 'str | Path') -> 'bytes'",
        "read_text": "(self, source: 'str | Path') -> 'str'",
        "readable": "(self, source: 'str | Path') -> 'Path'",
        "root": "attribute",
        "safe_name": "(name: 'str') -> 'str'",
        "write_bytes": "(self, destination: 'str | Path', data: 'bytes') -> 'Path'",
        "write_text": "(self, destination: 'str | Path', text: 'str') -> 'Path'",
    },
    "KV": {
        "delete": "(self, key: 'str') -> 'None'",
        "get": "(self, key: 'str', default: 'Any' = None) -> 'Any'",
        "set": "(self, key: 'str', value: 'Any') -> 'None'",
    },
    "Tasks": {
        "cancel": "(self, task_id: 'int') -> 'bool'",
        "create": "(self, payload: 'dict[str, Any]') -> 'int'",
        "get": "(self, task_id: 'int') -> 'Obj | None'",
        "progress": "(self, task_id: 'int', text: 'str') -> 'None'",
    },
    "BotApi": {"call": "(self, method: 'str', **params: 'Any') -> 'Any'"},
    "Obj": {
        "get": "(self, key: 'str', default: 'Any' = None) -> 'Any'",
        "to_dict": "(self) -> 'dict'",
    },
}


def test_sdk_classes_match_snapshot():
    classes = {
        "Context": sdk.Context,
        "Files": sdk.Files,
        "KV": sdk.KV,
        "Tasks": sdk.Tasks,
        "BotApi": sdk.BotApi,
        "Obj": sdk.Obj,
    }
    actual = {name: surface(cls) for name, cls in classes.items()}
    assert actual == SNAPSHOT, MESSAGE


def test_media_matches_snapshot():
    assert list(inspect.signature(media.download).parameters) == [
        "ctx",
        "url",
        "folder",
        "audio_only",
        "format",
        "max_size_mb",
        "cookies",
        "filename",
        "status",
        "messages",
        "options",
    ], MESSAGE
    assert [f.name for f in dataclasses.fields(media.Download)] == [
        "path",
        "title",
        "url",
        "size",
        "message_id",
        "info",
    ], MESSAGE
    assert [f.name for f in dataclasses.fields(sdk.ShellResult)] == [
        "returncode",
        "stdout",
        "stderr",
    ], MESSAGE
    assert list(media.SEND_METHODS) == [
        "video",
        "audio",
        "photo",
        "animation",
        "voice",
        "document",
    ], MESSAGE
    assert list(media.DEFAULT_MESSAGES) == [
        "start",
        "progress",
        "processing",
        "done",
        "failed",
    ], MESSAGE

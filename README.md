# bot-runner

The execution service for [bot-orchestrator](https://github.com/hametovbr/bot-orchestrator):
it runs user-written Telegram bot handlers.

> **Status:** 0.3. Deploy it with the `docker-compose.yml` from bot-orchestrator.

## How it works

- The orchestrator sends `POST /run` with the update, the bot, the handler code
  and its secrets. `kind: "handler"` calls `handle(update, ctx)`; `kind: "task"`
  runs a background task function started with `ctx.background(...)`.
- The runner starts a fresh Python process for every run, with a time limit
  (`HANDLER_TIMEOUT` for handlers, `TASK_TIMEOUT` for tasks) and a memory limit
  (`HANDLER_MEMORY_MB`). A crash or a hang only affects that run.
- Handlers and tasks have separate concurrency limits (`MAX_CONCURRENT_RUNS`,
  `MAX_CONCURRENT_TASKS`), so long downloads never block replies.
- `POST /cancel/{run_id}` kills a running job.
- Shared modules sent with the job are importable as `shared.<name>`; tracebacks
  show their source lines.
- `GET/POST /packages` reports and installs extra Python packages into
  `PACKAGES_DIR` with uv. A new list goes into its own folder and becomes active
  only after a successful install; the runner's own dependencies keep their
  versions.
- Handler output (`print`, `ctx.log`) and every Bot API call are streamed back and
  shown in the orchestrator's update log. Bot tokens are masked.
- In a dry run, only `get*` Bot API methods reach Telegram; other calls are
  recorded and not sent.

The image includes `yt-dlp` (as a library and a command) and `ffmpeg`;
`ctx.download(url)` wraps yt-dlp with a progress message in the chat.

## Example handler

```python
import datetime as dt


async def handle(update, ctx):
    msg = update.message
    if msg and msg.document:
        day = dt.date.fromtimestamp(msg.date).isoformat()
        path = await ctx.files.local_path(msg.document.file_id)
        saved = ctx.files.move(path, f"inbox/{day}_{ctx.files.safe_name(msg.document.file_name)}")
        await ctx.reply(f"Saved {saved.name}")
```

The full SDK reference, with rules, limits and tested examples, is the
[handler skill](https://github.com/hametovbr/bot-orchestrator/tree/main/skills/bot-orchestrator-handlers)
in bot-orchestrator: an Agent Skill that AI agents can install to write handlers,
and plain Markdown for people. The
[architecture document](https://github.com/hametovbr/bot-orchestrator/blob/main/docs/ARCHITECTURE.md#handler-sdk)
has a summary.

Changes to the public SDK must update that skill; `tests/test_sdk_surface.py`
fails until the snapshot is updated together with it (see [AGENTS.md](AGENTS.md)).

## Configuration

All settings are environment variables; see [.env.example](.env.example).

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run uvicorn --factory bot_runner.server:app --reload   # needs RUNNER_SECRET
```

## Security

Handlers are arbitrary code from the orchestrator's UI. Process isolation protects
the system from handler bugs; it is not a sandbox for untrusted authors. See
[SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)

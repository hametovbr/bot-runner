# bot-runner

The execution service for [bot-orchestrator](https://github.com/hametovbr/bot-orchestrator):
it runs user-written Telegram bot handlers.

> **Status:** MVP. Deploy it with the `docker-compose.yml` from bot-orchestrator.

## How it works

- The orchestrator sends `POST /run` with the update, the bot, the handler code
  and its secrets.
- The runner starts a fresh Python process for the run, with a time limit
  (`HANDLER_TIMEOUT`) and a memory limit (`HANDLER_MEMORY_MB`). A crash or a hang
  only affects that run.
- Handler output (`print`, `ctx.log`) and every Bot API call are streamed back and
  shown in the orchestrator's update log. Bot tokens are masked.
- In a dry run, only `get*` Bot API methods reach Telegram; other calls are
  recorded and not sent.

The image includes `yt-dlp` (as a library and a command) and `ffmpeg`.

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

The SDK reference is in the
[architecture document](https://github.com/hametovbr/bot-orchestrator/blob/main/docs/ARCHITECTURE.md#handler-sdk)
and on the orchestrator's Handler tab.

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

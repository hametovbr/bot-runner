# bot-runner

The execution service for [bot-orchestrator](https://github.com/hametovbr/bot-orchestrator):
it runs user-written Telegram bot handlers in an isolated container.

> **Status:** early design stage. Nothing here is usable yet.

## What it does

- Receives an update, the bot it belongs to and a handler version from the
  orchestrator, and runs that handler with a timeout.
- Loads new handler versions without restarting.
- Gives handlers a small SDK (`ctx`) for Bot API calls, files on the shared
  volume, key-value storage, secrets, HTTP requests and external tools such as
  `yt-dlp`.
- Runs long tasks (downloads, conversions) in a background queue so the bot can
  reply right away and send the result later.

A failing or hanging handler only affects its own run: the orchestrator and
other bots keep working.

## Example handler

```python
async def handle(update, ctx):
    doc = update.message.document
    path = await ctx.files.local_path(doc.file_id)
    saved = ctx.files.move(path, f"inbox/{update.message.date:%Y-%m-%d}_{doc.file_name}")
    await ctx.reply(f"Saved: {saved}")
```

The SDK is a draft and will change. See the
[architecture document](https://github.com/hametovbr/bot-orchestrator/blob/main/docs/ARCHITECTURE.md)
in bot-orchestrator for the full design.

## Security

Handlers are arbitrary code. The runner is meant to run without the Docker
socket, with CPU and memory limits, and with write access only to the bot files
folder. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)

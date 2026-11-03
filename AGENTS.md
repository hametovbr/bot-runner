# Notes for AI assistants

## Project

bot-runner executes user-written Telegram bot handlers for
[bot-orchestrator](https://github.com/hametovbr/bot-orchestrator). The overall
design, including the handler SDK and the data model, lives in
[bot-orchestrator/docs/ARCHITECTURE.md](https://github.com/hametovbr/bot-orchestrator/blob/main/docs/ARCHITECTURE.md);
changes to the orchestrator–runner contract must be reflected there.

## Language rules

- Everything in the repository is in English: code, comments, docs, commit
  messages, issue and PR text.
- Any user-facing text (SDK errors, log messages) is in English.
- The owner may write to you in Russian; reply in the conversation in their
  language, but keep repository content in English.

## Conventions

- Python 3.12+, dependencies managed with `uv` (`pyproject.toml` + `uv.lock`).
- Format and lint with `ruff`; tests with `pytest`.
- Commit messages: imperative mood, short subject line (≤ 72 characters),
  optional body explaining why.
- Configuration comes from environment variables; document every variable in
  `.env.example`.
- The container image is built by GitHub Actions and pushed to
  `ghcr.io/hametovbr/bot-runner`.

## Invariants

- Every handler run has a timeout; a failing or hanging handler must not affect
  other runs.
- Handlers reach state, secrets and other bots only through the `ctx` SDK, never
  through direct database access.
- File access is scoped to the bot's own folder on the shared volume.
- The container runs without the Docker socket.

## Handler skill

The SDK in this repo is documented for handler authors, people and AI agents, by
the skill
[`skills/bot-orchestrator-handlers`](https://github.com/hametovbr/bot-orchestrator/tree/main/skills/bot-orchestrator-handlers)
in bot-orchestrator. Its rules for maintenance are in bot-orchestrator's
[AGENTS.md](https://github.com/hametovbr/bot-orchestrator/blob/main/AGENTS.md#handler-skill-skillsbot-orchestrator-handlers).

**Updating the skill is mandatory.** A change here that affects what handler code
sees is not done until the skill is updated in bot-orchestrator. That includes:

- `sdk.py`, `media.py`: `ctx` members, arguments, defaults, return values,
  exceptions, dry-run behavior, `ctx.download` options and status texts;
- `worker.py`, `shared_modules.py`: how handlers and tasks are called, imports,
  `shared.<name>` modules, `print` and logging;
- `packages.py` and the image: preinstalled libraries and programs (`yt-dlp`,
  `ffmpeg`), package rules;
- `config.py`: timeouts, memory, concurrency, folders and their defaults.

`tests/test_sdk_surface.py` holds a snapshot of the public SDK. When it fails,
update the skill, `docs/ARCHITECTURE.md` and `sdk_hints.js` in bot-orchestrator
first, then the snapshot. Never update the snapshot alone.

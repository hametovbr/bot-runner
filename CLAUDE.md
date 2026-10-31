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

# Changelog

Versions follow [Semantic Versioning](https://semver.org); until 1.0 a minor
version may contain breaking changes. Release `X.Y.Z` of bot-runner is meant to
run with release `X.Y.Z` of
[bot-orchestrator](https://github.com/hametovbr/bot-orchestrator/blob/main/CHANGELOG.md).

## [0.4.0] - 2026-11-03

### Added

- Environment variables for handlers: on start the runner collects its variables
  named `BOTENV_<NAME>` and passes them to every handler, background task and
  `ctx.shell` program as `<NAME>`. The prefixed originals are removed; empty or
  invalid names, `PATH`, `PYTHON*`, `LD_*` and runner settings are skipped with a
  warning. The container log lists the names passed.
- Versioned releases: pushing a tag `vX.Y.Z` checks it against `pyproject.toml`,
  publishes the image as `X.Y.Z` and `X.Y` and creates a GitHub release from this
  file.

### Changed

- The `latest` image tag is published from `main` only.

## [0.3.0]

- Extra Python packages installed with uv; shared modules (`shared.<name>`).

## [0.2.0]

- Background tasks with cancel, `ctx.download` (yt-dlp) and `ctx.reply_file`.

## [0.1.0]

- MVP: a fresh process per handler call with time and memory limits, the `ctx` SDK.

# Security policy

## Threat model

bot-runner executes arbitrary handler code written in the
[bot-orchestrator](https://github.com/hametovbr/bot-orchestrator) web UI. Isolation
here protects the rest of the system from handler bugs (crashes, hangs, runaway
memory); it is not a sandbox for untrusted authors. Only trusted people should
have access to the orchestrator UI.

Deployment rules:

- Never mount the Docker socket into the runner container.
- Set CPU and memory limits on the container.
- Mount only the bot files folder and the local Bot API data directory, at the
  same paths as in the orchestrator.
- Publish no ports; the runner talks only to the orchestrator and
  `telegram-bot-api` on the internal Docker network.
- Do not give the runner direct access to the orchestrator database. Handlers
  get state and secrets only through the SDK.

## Reporting a vulnerability

Please do not open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/hametovbr/bot-runner/security/advisories/new)
instead.

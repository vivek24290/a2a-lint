# a2a-lint

Lint your A2A agents — validate agent cards against the spec, run live
conformance probes in CI, and earn a conformance badge. Playground UI included.

Built for the [A2A (Agent2Agent) protocol](https://a2a-protocol.org): the open
standard (Google → Linux Foundation) that lets AI agents discover and delegate
work to each other. Your agent speaks A2A the moment its card and endpoints
conform — a2a-lint makes sure they do, **and keep doing so on every push**.

## Why a2a-lint

Inspecting an agent once is easy (the official
[a2a-inspector](https://github.com/a2aproject/a2a-inspector) does it well).
Staying conformant is the hard part: a renamed skill, a card whose advertised
`url` only resolves inside your compose network, a `message/send` that stopped
returning Tasks — all invisible until another agent fails against yours.
a2a-lint is built around **continuous** conformance:

- **CLI** with CI-friendly exit codes — wire it into any pipeline
- **GitHub Action** — every push checks the card *and* does a live round trip
- **Live badge** — your README shows your current conformance grade
- **Playground** — when something fails, debug it interactively with full
  wire-level detail

## Quick start

```bash
docker compose up --build -d      # playground on http://localhost:8090
```

Paste any agent's base URL: you get its Agent Card rendered and graded
(field-by-field findings with fix hints), an interactive JSON-RPC
`message/send` console, `tasks/get` inspection, an SSE `message/stream`
viewer, and shareable permalinks of inspection snapshots.

> Inspecting agents that run on your host from inside the container? Use
> `http://host.docker.internal:<port>` (mapped via `extra_hosts`).

## CLI

```bash
cd server && pip install -r requirements.txt
python a2a_lint.py https://my-agent.example.com --live
```

```
✔ Weather Agent — card at https://my-agent.example.com/.well-known/agent-card.json (12 ms)
✔ live message/send round trip in 48 ms
GRADE B — 0 error(s), 1 warning(s)
  WARN  url: Endpoint URL is plain http.
        ↳ Use https in production; A2A messages can carry sensitive content.
```

| Flag | Effect |
|---|---|
| `--live` | also send a real `message/send` and verify the result is a Task/Message |
| `--strict` | warnings fail the lint too |
| `--json` | machine-readable output |

Exit codes: **0** conformant · **1** findings · **2** unreachable/not A2A.

`--live` catches the classic deployment bug: a card that advertises a URL its
consumers can't reach (e.g. a container-internal hostname on a public card).

## GitHub Action

```yaml
jobs:
  a2a-conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: vivek24290/a2a-lint@develop
        with:
          url: https://my-agent.example.com
          live: "true"
          strict: "false"
```

## Conformance badge

Live-graded SVG (5-minute cache) served by your playground instance:

```markdown
![A2A conformance](https://<your-playground-host>/api/badge?url=https://my-agent.example.com)
```

## What gets checked

Required card fields (`name`, `description`, `url`, `version`, `capabilities`,
`defaultInputModes/OutputModes`, `skills` and per-skill fields), transport
declarations, deprecated card paths, http-vs-https, missing tags/examples,
undeclared auth — each finding carries a level (`error`/`warn`/`info`) and a
concrete fix hint. Grades: A (clean) → F. See `server/card_validator.py`; the
same core powers the CLI, the Action, the badge, and the playground.

## Architecture

```
server/
├── a2a_client.py      A2A client: card discovery, message/send, tasks/get, streaming
├── card_validator.py  spec checks -> structured findings (the shared core)
├── a2a_lint.py        CLI (exit codes 0/1/2)
├── main.py            FastAPI: playground API, SSE proxy, permalinks, badge
└── static/index.html  playground UI
action.yml             GitHub Action wrapping the CLI
```

No LLM calls, no telemetry, no external services. Permalink snapshots are
stored in a local SQLite file under `./data/`.

## Roadmap

- [ ] `pip install a2a-lint` packaging
- [ ] `message/stream` conformance checks in the CLI
- [ ] Security-scheme validation (per-scheme checks)
- [ ] **a2a-watch** — hosted monitoring: scheduled probes of your deployed
      agents, alerting, uptime history, public status pages

## License

[MIT](LICENSE)

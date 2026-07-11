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
pip install git+https://github.com/vivek24290/a2a-lint@develop   # PyPI release coming
a2a-lint https://my-agent.example.com --live
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
| `--live` | also send a real `message/send` and verify the result is a Task/Message; if the card declares `streaming: true`, `message/stream` SSE conformance is checked too |
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
      - uses: vivek24290/a2a-lint@v1
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

## a2a-watch monitor (MVP)

Open **/status.html** on your playground instance: register any agent and it
gets probed on a schedule (card fetch + validation + live `message/send` —
the same check the CLI runs). You get UP/DOWN state, 24h uptime, conformance
grade, latency, and a **webhook alert** (JSON `down`/`recovered` events —
point it at Slack/Discord/anything) the moment an agent breaks or recovers.

```bash
curl -X POST localhost:8090/api/monitor/agents -H 'Content-Type: application/json' \
  -d '{"url": "https://my-agent.example.com", "intervalSeconds": 300,
       "webhook": "https://hooks.slack.com/services/..."}'
```

History lives in `data/monitor.db`. If an agent's card advertises an
unreachable `url`, the monitor falls back to the registered URL and reports
the bad card instead of a false DOWN.

## Architecture

```
a2a_lint/              the pip-installable package (shared core)
├── client.py          A2A client: card discovery, message/send, tasks/get, streaming
├── validator.py       spec checks -> structured findings
└── cli.py             the a2a-lint command (exit codes 0/1/2)
server/
├── main.py            FastAPI: playground API, SSE proxy, permalinks, badge, monitor API
├── monitor.py         a2a-watch core: scheduler, uptime history, webhook alerts
└── static/            playground UI (index.html) + monitor UI (status.html)
action.yml             GitHub Action wrapping the CLI
```

No LLM calls, no telemetry, no external services. Permalink snapshots and
monitor history are SQLite files under `./data/`.

## Roadmap

- [x] `pip install a2a-lint` packaging (PyPI publish via `.github/workflows/publish.yml`)
- [x] `message/stream` conformance checks in the CLI
- [x] Security-scheme validation
- [x] **a2a-watch** monitor MVP: scheduled probes, uptime history, webhook alerts
- [ ] Hosted a2a-watch: accounts, public status pages, e-mail alerts, paid tiers
- [ ] Public playground instance (VPS + domain)

## License

[MIT](LICENSE)

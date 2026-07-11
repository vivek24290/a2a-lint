"""
a2a-lint — playground backend.

Thin proxy in front of a2a_client + card_validator: the browser can't call
arbitrary agents cross-origin, so it talks to these endpoints instead and the
server does the A2A calls, returning full wire detail.

  POST /api/inspect {url}                  -> card + validation findings
  POST /api/send    {endpoint, text}       -> message/send round trip
  POST /api/task    {endpoint, taskId}     -> tasks/get round trip
  GET  /            -> the playground UI (static/)

NOTE for public hosting: this proxy fetches URLs the user supplies (that is
its purpose — including agents on private networks during development). Before
exposing an instance to the internet, consider restricting target hosts.
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from a2a_lint import A2AClient, A2AProbeError, build_rpc_request, summarize, validate_card
from monitor import Monitor
from store import SessionStore

DATA_DIR = os.environ.get("DATA_DIR", "data")
sessions = SessionStore(os.path.join(DATA_DIR, "sessions.db"))
monitor = Monitor(os.path.join(DATA_DIR, "monitor.db"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = asyncio.create_task(monitor.run_loop())
    yield
    scheduler.cancel()


app = FastAPI(title="a2a-lint", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class InspectRequest(BaseModel):
    url: str


class SendRequest(BaseModel):
    endpoint: str
    text: str
    contextId: str | None = None


class TaskRequest(BaseModel):
    endpoint: str
    taskId: str


@app.post("/api/inspect")
async def inspect(req: InspectRequest):
    client = A2AClient(req.url)
    try:
        probe = await client.fetch_card()
    except A2AProbeError as exc:
        return {"ok": False, "error": str(exc), "attempts": exc.attempts}
    findings = validate_card(probe["card"], deprecated_path=probe["deprecatedPath"])
    return {"ok": True, **probe, "findings": findings, "summary": summarize(findings)}


@app.post("/api/send")
async def send(req: SendRequest):
    client = A2AClient(req.endpoint)
    try:
        result = await client.send_message(req.endpoint, req.text, req.contextId)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **result}


@app.post("/api/task")
async def task(req: TaskRequest):
    client = A2AClient(req.endpoint)
    try:
        result = await client.get_task(req.endpoint, req.taskId)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **result}


@app.post("/api/stream")
async def stream(req: SendRequest):
    """Proxy a message/stream call, passing the agent's SSE through verbatim.
    The first event (event: request) carries the JSON-RPC request we sent so
    the UI can display the full wire exchange."""
    rpc_request = build_rpc_request("message/stream", req.text, req.contextId)

    async def relay():
        yield f"event: request\ndata: {json.dumps(rpc_request)}\n\n"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", req.endpoint, json=rpc_request) as resp:
                    async for line in resp.aiter_lines():
                        yield line + "\n"
        except httpx.HTTPError as exc:
            yield f'event: proxy-error\ndata: {json.dumps({"error": f"{type(exc).__name__}: {exc}"})}\n\n'

    return StreamingResponse(relay(), media_type="text/event-stream")


class ShareRequest(BaseModel):
    url: str
    inspect: dict


@app.post("/api/share")
async def share(req: ShareRequest):
    return {"id": sessions.save({"url": req.url, "inspect": req.inspect})}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    payload = sessions.load(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No such session")
    return payload


# --- a2a-watch monitor (MVP): scheduled probes + webhook alerts ------------

class MonitorAddRequest(BaseModel):
    url: str
    name: str | None = None
    intervalSeconds: int = 300
    webhook: str | None = None


@app.post("/api/monitor/agents")
async def monitor_add(req: MonitorAddRequest):
    return monitor.add_agent(req.url, req.name, req.intervalSeconds, req.webhook)


@app.get("/api/monitor/agents")
async def monitor_list():
    return monitor.list_agents()


@app.delete("/api/monitor/agents/{agent_id}")
async def monitor_remove(agent_id: int):
    if not monitor.remove_agent(agent_id):
        raise HTTPException(status_code=404, detail="No such monitored agent")
    return {"removed": agent_id}


@app.get("/api/monitor/agents/{agent_id}/checks")
async def monitor_checks(agent_id: int, limit: int = 50):
    return monitor.checks(agent_id, min(limit, 500))


# --- Conformance badge: live-graded SVG for READMEs ------------------------

BADGE_COLORS = {"A": "#3fb97f", "B": "#7cc46b", "C": "#dfb317", "D": "#fe7d37", "F": "#e05d44"}
_badge_cache: dict[str, tuple[float, str]] = {}
BADGE_TTL_SECONDS = 300


def render_badge(label: str, value: str, color: str) -> str:
    left_w = 6 * len(label) + 14
    right_w = 6 * len(value) + 14
    total = left_w + right_w
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{label}: {value}">
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<rect rx="3" width="{total}" height="20" fill="#555"/>
<rect rx="3" x="{left_w}" width="{right_w}" height="20" fill="{color}"/>
<rect rx="3" width="{total}" height="20" fill="url(#s)"/>
<g fill="#fff" text-anchor="middle" font-family="Verdana,sans-serif" font-size="11">
<text x="{left_w / 2}" y="14">{label}</text>
<text x="{left_w + right_w / 2}" y="14">{value}</text>
</g></svg>'''


@app.get("/api/badge")
async def badge(url: str):
    now = time.time()
    cached = _badge_cache.get(url)
    if cached and now - cached[0] < BADGE_TTL_SECONDS:
        svg = cached[1]
    else:
        try:
            probe = await A2AClient(url, timeout=6).fetch_card()
            grade = summarize(
                validate_card(probe["card"], deprecated_path=probe["deprecatedPath"])
            )["grade"]
            svg = render_badge("A2A", f"grade {grade}", BADGE_COLORS[grade])
        except (A2AProbeError, httpx.HTTPError):
            svg = render_badge("A2A", "unreachable", "#9f9f9f")
        _badge_cache[url] = (now, svg)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": f"max-age={BADGE_TTL_SECONDS}"},
    )


# Mounted last so /api/* wins; html=True serves index.html at /.
app.mount("/", StaticFiles(directory="static", html=True), name="static")

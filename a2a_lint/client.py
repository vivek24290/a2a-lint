"""
Generalized A2A client — extracted from a2a-demo's planner agent.

Talks to ANY A2A agent given only its base URL: discovers the agent card,
sends JSON-RPC messages, fetches tasks. Every call returns wire-level detail
(exact URL, request, response, latency) because a2a-lint's whole job is to
show developers what actually happened on the wire.

This module is the shared core of all a2a-lint layers:
playground, lint CLI/CI action, and (future) a2a-watch monitor probes.
"""
import json
import time
import uuid

import httpx

# Current spec path first, pre-0.3.0 path as fallback.
CARD_PATHS = ["/.well-known/agent-card.json", "/.well-known/agent.json"]


def build_rpc_request(method: str, text: str, context_id: str | None = None) -> dict:
    """JSON-RPC envelope for message/send or message/stream."""
    message: dict = {
        "role": "user",
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}],
    }
    if context_id:
        message["contextId"] = context_id
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": {"message": message},
    }


class A2AProbeError(Exception):
    """Raised when an agent card cannot be retrieved. Carries per-URL detail."""

    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


class A2AClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def fetch_card(self) -> dict:
        """Discover the agent card. Returns card + probe metadata."""
        attempts: list[dict] = []
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            for path in CARD_PATHS:
                url = self.base_url + path
                start = time.perf_counter()
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as exc:
                    attempts.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                latency_ms = round((time.perf_counter() - start) * 1000)
                if resp.status_code != 200:
                    attempts.append({"url": url, "error": f"HTTP {resp.status_code}"})
                    continue
                try:
                    card = resp.json()
                except ValueError:
                    attempts.append({"url": url, "error": "200 OK but body is not JSON"})
                    continue
                if not isinstance(card, dict):
                    attempts.append({"url": url, "error": "JSON body is not an object"})
                    continue
                return {
                    "card": card,
                    "cardUrl": url,
                    "latencyMs": latency_ms,
                    "deprecatedPath": path == "/.well-known/agent.json",
                    "attempts": attempts,
                }
        raise A2AProbeError(
            f"No agent card found at {self.base_url} — this does not look like an A2A agent.",
            attempts,
        )

    async def send_message(self, endpoint: str, text: str, context_id: str | None = None) -> dict:
        """JSON-RPC message/send. Returns request, response and latency."""
        return await self._rpc(endpoint, build_rpc_request("message/send", text, context_id))

    async def stream_message(self, endpoint: str, text: str, max_events: int = 50) -> dict:
        """JSON-RPC message/stream over SSE. Collects events until a final
        status-update, max_events, or stream end. Returns wire-level detail."""
        rpc_request = build_rpc_request("message/stream", text)
        events: list[dict] = []
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            async with client.stream("POST", endpoint, json=rpc_request) as resp:
                status = resp.status_code
                content_type = resp.headers.get("content-type", "")
                if status == 200 and "text/event-stream" in content_type:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            payload = json.loads(line[5:].strip())
                        except ValueError:
                            events.append({"invalidJson": line[:200]})
                            continue
                        events.append(payload)
                        result = payload.get("result") or {}
                        if result.get("final") or len(events) >= max_events:
                            break
        return {
            "endpoint": endpoint,
            "request": rpc_request,
            "events": events,
            "httpStatus": status,
            "contentType": content_type,
            "latencyMs": round((time.perf_counter() - start) * 1000),
        }

    async def get_task(self, endpoint: str, task_id: str) -> dict:
        """JSON-RPC tasks/get. Returns request, response and latency."""
        rpc_request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        return await self._rpc(endpoint, rpc_request)

    async def _rpc(self, endpoint: str, rpc_request: dict) -> dict:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            start = time.perf_counter()
            resp = await client.post(endpoint, json=rpc_request)
            latency_ms = round((time.perf_counter() - start) * 1000)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"nonJsonBody": resp.text[:2000]}
        return {
            "endpoint": endpoint,
            "request": rpc_request,
            "response": payload,
            "httpStatus": resp.status_code,
            "latencyMs": latency_ms,
        }

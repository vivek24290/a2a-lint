"""
a2a-watch monitor core (MVP): register agents, probe them on a schedule,
keep uptime history, fire a webhook when an agent goes down or recovers.

A probe = the same conformance check a2a-lint runs in CI: fetch + validate
the card, then a live message/send round trip. UP means "another agent could
actually delegate work to this one right now" — card reachable AND the live
round trip succeeded. Conformance findings are recorded alongside as a grade.
"""
import asyncio
import json
import sqlite3
import threading
import time

import httpx

from a2a_lint import A2AClient, A2AProbeError, summarize, validate_card

LOOP_TICK_SECONDS = 10
MIN_INTERVAL_SECONDS = 30


class Monitor:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS agents ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, "
                "name TEXT, interval_s INTEGER NOT NULL, webhook TEXT, "
                "created REAL NOT NULL, next_run REAL NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS checks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER NOT NULL, "
                "ts REAL NOT NULL, ok INTEGER NOT NULL, grade TEXT, "
                "errors INTEGER, warnings INTEGER, latency_ms INTEGER, detail TEXT)"
            )
            self._conn.commit()

    # ---- registry ---------------------------------------------------------

    def add_agent(self, url: str, name: str | None, interval_s: int, webhook: str | None) -> dict:
        interval_s = max(int(interval_s), MIN_INTERVAL_SECONDS)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO agents (url, name, interval_s, webhook, created, next_run) "
                "VALUES (?, ?, ?, ?, ?, 0)",  # next_run=0 -> probed on the next tick
                (url.rstrip("/"), name, interval_s, webhook, time.time()),
            )
            self._conn.commit()
            return {"id": cur.lastrowid, "url": url.rstrip("/"), "name": name,
                    "intervalSeconds": interval_s, "webhook": webhook}

    def remove_agent(self, agent_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            self._conn.execute("DELETE FROM checks WHERE agent_id = ?", (agent_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_agents(self) -> list[dict]:
        day_ago = time.time() - 86400
        with self._lock:
            agents = self._conn.execute(
                "SELECT id, url, name, interval_s, webhook FROM agents ORDER BY id"
            ).fetchall()
            out = []
            for agent_id, url, name, interval_s, webhook in agents:
                last = self._conn.execute(
                    "SELECT ts, ok, grade, errors, warnings, latency_ms FROM checks "
                    "WHERE agent_id = ? ORDER BY ts DESC LIMIT 1", (agent_id,)
                ).fetchone()
                total, up = self._conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM checks "
                    "WHERE agent_id = ? AND ts > ?", (agent_id, day_ago)
                ).fetchone()
                recent = self._conn.execute(
                    "SELECT ok FROM checks WHERE agent_id = ? ORDER BY ts DESC LIMIT 20",
                    (agent_id,)
                ).fetchall()
                out.append({
                    "id": agent_id, "url": url, "name": name,
                    "intervalSeconds": interval_s, "webhook": webhook,
                    "lastCheck": None if last is None else {
                        "ts": last[0], "ok": bool(last[1]), "grade": last[2],
                        "errors": last[3], "warnings": last[4], "latencyMs": last[5],
                    },
                    "uptime24h": None if total == 0 else round(100.0 * up / total, 1),
                    "recent": [bool(r[0]) for r in reversed(recent)],
                })
            return out

    def checks(self, agent_id: int, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, ok, grade, errors, warnings, latency_ms, detail FROM checks "
                "WHERE agent_id = ? ORDER BY ts DESC LIMIT ?", (agent_id, limit)
            ).fetchall()
        return [{"ts": r[0], "ok": bool(r[1]), "grade": r[2], "errors": r[3],
                 "warnings": r[4], "latencyMs": r[5], "detail": r[6]} for r in rows]

    # ---- probing ----------------------------------------------------------

    async def probe(self, url: str) -> dict:
        client = A2AClient(url, timeout=10)
        try:
            card_probe = await client.fetch_card()
        except A2AProbeError as exc:
            return {"ok": False, "grade": None, "errors": None, "warnings": None,
                    "latencyMs": None, "detail": str(exc)}
        findings = validate_card(card_probe["card"], deprecated_path=card_probe["deprecatedPath"])
        summary = summarize(findings)

        # Probe the card's advertised endpoint first (what real clients use);
        # fall back to the registered URL so a bad card.url degrades to a
        # reported problem instead of a false DOWN.
        endpoints = [card_probe["card"].get("url") or url]
        registered = url.rstrip("/") + "/"
        if endpoints[0].rstrip("/") + "/" != registered:
            endpoints.append(registered)

        ok, detail, latency = False, None, None
        for i, endpoint in enumerate(endpoints):
            try:
                result = await client.send_message(endpoint, "a2a-watch health probe")
                ok = result["httpStatus"] == 200 and (
                    result["response"].get("result", {}).get("kind") in ("task", "message")
                )
                latency = result["latencyMs"]
                if ok:
                    if i > 0:
                        detail = (f"card.url {endpoints[0]} is unreachable from the monitor; "
                                  "probe succeeded via the registered URL. Clients using the "
                                  "card will fail — fix the advertised URL.")
                    break
                detail = f"message/send returned HTTP {result['httpStatus']}"
            except httpx.HTTPError as exc:
                detail = f"message/send failed: {type(exc).__name__}: {exc}"
        return {"ok": ok, "grade": summary["grade"], "errors": summary["errors"],
                "warnings": summary["warnings"], "latencyMs": latency, "detail": detail}

    def _record(self, agent_id: int, result: dict) -> bool | None:
        """Store a check; returns the PREVIOUS ok state (None if first check)."""
        with self._lock:
            prev = self._conn.execute(
                "SELECT ok FROM checks WHERE agent_id = ? ORDER BY ts DESC LIMIT 1",
                (agent_id,)
            ).fetchone()
            self._conn.execute(
                "INSERT INTO checks (agent_id, ts, ok, grade, errors, warnings, latency_ms, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (agent_id, time.time(), int(result["ok"]), result["grade"],
                 result["errors"], result["warnings"], result["latencyMs"], result["detail"]),
            )
            self._conn.commit()
        return None if prev is None else bool(prev[0])

    async def _alert(self, agent: dict, event: str, result: dict):
        if not agent.get("webhook"):
            return
        payload = {
            "source": "a2a-watch", "event": event,
            "agent": agent.get("name") or agent["url"], "url": agent["url"],
            "grade": result["grade"], "detail": result["detail"],
            "ts": time.time(),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(agent["webhook"], json=payload)
        except httpx.HTTPError as exc:
            print(f"[a2a-watch] webhook delivery failed for {agent['url']}: {exc}")

    async def run_loop(self):
        """Background scheduler: probe every agent whose next_run has passed."""
        while True:
            now = time.time()
            with self._lock:
                due = self._conn.execute(
                    "SELECT id, url, name, interval_s, webhook FROM agents WHERE next_run <= ?",
                    (now,)
                ).fetchall()
            for agent_id, url, name, interval_s, webhook in due:
                agent = {"id": agent_id, "url": url, "name": name, "webhook": webhook}
                result = await self.probe(url)
                prev_ok = self._record(agent_id, result)
                with self._lock:
                    self._conn.execute(
                        "UPDATE agents SET next_run = ? WHERE id = ?",
                        (time.time() + interval_s, agent_id),
                    )
                    self._conn.commit()
                if prev_ok is True and not result["ok"]:
                    await self._alert(agent, "down", result)
                elif prev_ok is False and result["ok"]:
                    await self._alert(agent, "recovered", result)
            await asyncio.sleep(LOOP_TICK_SECONDS)

#!/usr/bin/env python3
"""
a2a-lint — conformance linter for A2A agents.

    python a2a_lint.py <agent-base-url> [--live] [--strict] [--json]

Checks the agent card against the A2A spec (same validator core as the
bundled playground). With --live it also performs a real message/send
round trip and verifies the result is a well-formed Task or Message.

Exit codes (CI-friendly):
    0  conformant (warnings allowed unless --strict)
    1  findings at error level (or warnings with --strict)
    2  agent unreachable / no agent card found
"""
import argparse
import asyncio
import json
import sys

from a2a_client import A2AClient, A2AProbeError
from card_validator import summarize, validate_card

RESET, BOLD, RED, YELLOW, BLUE, GREEN, DIM = (
    "\033[0m", "\033[1m", "\033[31m", "\033[33m", "\033[34m", "\033[32m", "\033[2m",
)
LEVEL_COLOR = {"error": RED, "warn": YELLOW, "info": BLUE}


def paint(s: str, color: str, enabled: bool) -> str:
    return f"{color}{s}{RESET}" if enabled else s


async def lint(url: str, live: bool, timeout: float) -> tuple[list[dict], dict]:
    client = A2AClient(url, timeout=timeout)
    probe = await client.fetch_card()
    findings = validate_card(probe["card"], deprecated_path=probe["deprecatedPath"])
    meta = {
        "cardUrl": probe["cardUrl"],
        "latencyMs": probe["latencyMs"],
        "agent": probe["card"].get("name"),
    }

    if live:
        endpoint = probe["card"].get("url") or url
        try:
            result = await client.send_message(endpoint, "a2a-lint conformance probe")
            response = result["response"]
            if result["httpStatus"] != 200:
                findings.append({
                    "level": "error", "field": "(live)",
                    "message": f"message/send returned HTTP {result['httpStatus']}.",
                    "hint": "JSON-RPC endpoints should return 200 with errors in the body.",
                })
            elif "error" in response:
                findings.append({
                    "level": "error", "field": "(live)",
                    "message": f"message/send returned JSON-RPC error {response['error'].get('code')}: "
                               f"{response['error'].get('message')}",
                    "hint": "",
                })
            elif response.get("result", {}).get("kind") not in ("task", "message"):
                findings.append({
                    "level": "error", "field": "(live)",
                    "message": "message/send result is neither a Task nor a Message.",
                    "hint": 'The result object must carry "kind": "task" or "kind": "message".',
                })
            else:
                meta["liveRoundTripMs"] = result["latencyMs"]
        except Exception as exc:  # noqa: BLE001 — any transport failure is a finding
            findings.append({
                "level": "error", "field": "(live)",
                "message": f"message/send to {endpoint} failed: {type(exc).__name__}: {exc}",
                "hint": "The card's url must be reachable by its consumers "
                        "(a container-internal hostname on a public card is a common mistake).",
            })

    return findings, meta


def main() -> int:
    parser = argparse.ArgumentParser(prog="a2a-lint", description=__doc__)
    parser.add_argument("url", help="Agent base URL, e.g. https://agent.example.com")
    parser.add_argument("--live", action="store_true", help="also do a message/send round trip")
    parser.add_argument("--strict", action="store_true", help="warnings also fail the lint")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    color = sys.stdout.isatty() and not args.as_json

    try:
        findings, meta = asyncio.run(lint(args.url, args.live, args.timeout))
    except A2AProbeError as exc:
        if args.as_json:
            print(json.dumps({"ok": False, "error": str(exc), "attempts": exc.attempts}, indent=2))
        else:
            print(paint(f"✖ {exc}", RED, color))
            for attempt in exc.attempts:
                print(f"  tried {attempt['url']} → {attempt['error']}")
        return 2

    summary = summarize(findings)
    if args.as_json:
        print(json.dumps({"ok": True, **meta, "summary": summary, "findings": findings}, indent=2))
    else:
        print(paint(f"✔ {meta['agent'] or '(unnamed agent)'}", GREEN, color)
              + f" — card at {meta['cardUrl']} ({meta['latencyMs']} ms)")
        if "liveRoundTripMs" in meta:
            print(paint(f"✔ live message/send round trip in {meta['liveRoundTripMs']} ms", GREEN, color))
        grade_color = GREEN if summary["grade"] in "AB" else (YELLOW if summary["grade"] == "C" else RED)
        print(paint(f"GRADE {summary['grade']}", BOLD + grade_color, color)
              + f" — {summary['errors']} error(s), {summary['warnings']} warning(s)")
        for f in findings:
            print(f"  {paint(f['level'].upper().ljust(5), LEVEL_COLOR[f['level']], color)} "
                  f"{f['field']}: {f['message']}")
            if f["hint"]:
                print(paint(f"        ↳ {f['hint']}", DIM, color))

    if summary["errors"] or (args.strict and summary["warnings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

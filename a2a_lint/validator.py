"""
Agent Card validator — checks a card against the A2A specification
(https://a2a-protocol.org/latest/specification/) and returns structured
findings a developer can act on.

Finding levels:
  error — violates the spec; many A2A clients will fail on this
  warn  — legal but risky / deprecated / bad practice
  info  — worth knowing, no action strictly required

Shared by the playground (/api/inspect), a2a-lint (phase 2) and the
monitor's conformance probes (phase 3).
"""

KNOWN_TRANSPORTS = {"JSONRPC", "GRPC", "HTTP+JSON"}
KNOWN_SECURITY_TYPES = {"apiKey", "http", "oauth2", "openIdConnect", "mutualTLS"}


def validate_card(card: dict, deprecated_path: bool = False) -> list[dict]:
    findings: list[dict] = []

    def add(level: str, field: str, message: str, hint: str = ""):
        findings.append({"level": level, "field": field, "message": message, "hint": hint})

    if deprecated_path:
        add(
            "warn", "(location)",
            "Card was served from the deprecated /.well-known/agent.json path.",
            "Serve it at /.well-known/agent-card.json (spec ≥ 0.3.0); keep the old path as an alias.",
        )

    # --- Required top-level fields -------------------------------------
    for field in ("name", "description", "url", "version"):
        value = card.get(field)
        if value is None:
            add("error", field, f"Required field '{field}' is missing.",
                "Every A2A agent card must declare this field.")
        elif not isinstance(value, str) or not value.strip():
            add("error", field, f"'{field}' must be a non-empty string.")

    if "protocolVersion" not in card:
        add("warn", "protocolVersion",
            "No protocolVersion declared.",
            "Declare the A2A spec version you implement, e.g. \"0.3.0\", so clients can negotiate.")

    # --- url sanity ------------------------------------------------------
    url = card.get("url")
    if isinstance(url, str) and url.startswith("http://"):
        host = url.split("//", 1)[1].split("/", 1)[0].split(":")[0]
        if host in ("localhost", "127.0.0.1") or host.endswith(".local"):
            add("info", "url", "Endpoint uses plain http on a local address — fine for development.")
        else:
            add("warn", "url", "Endpoint URL is plain http.",
                "Use https in production; A2A messages can carry sensitive content.")

    # --- transport -------------------------------------------------------
    transport = card.get("preferredTransport")
    if transport is None:
        add("warn", "preferredTransport",
            "No preferredTransport declared.",
            'Declare the transport of the main url, e.g. "JSONRPC" — clients assume JSONRPC but should not have to guess.')
    elif transport not in KNOWN_TRANSPORTS:
        add("error", "preferredTransport",
            f"Unknown transport '{transport}'.",
            f"Expected one of: {', '.join(sorted(KNOWN_TRANSPORTS))}.")

    # --- capabilities ----------------------------------------------------
    capabilities = card.get("capabilities")
    if capabilities is None:
        add("error", "capabilities", "Required field 'capabilities' is missing.",
            'Declare at least {"streaming": false} so clients know what they can rely on.')
    elif not isinstance(capabilities, dict):
        add("error", "capabilities", "'capabilities' must be an object.")
    else:
        for cap in ("streaming", "pushNotifications"):
            if cap in capabilities and not isinstance(capabilities[cap], bool):
                add("error", f"capabilities.{cap}", f"'{cap}' must be a boolean.")
        if "streaming" not in capabilities:
            add("info", "capabilities.streaming",
                "streaming not declared; clients will assume it is unsupported.")

    # --- input/output modes ----------------------------------------------
    for field in ("defaultInputModes", "defaultOutputModes"):
        modes = card.get(field)
        if modes is None:
            add("error", field, f"Required field '{field}' is missing.",
                'Declare supported MIME types, e.g. ["text/plain"].')
        elif not isinstance(modes, list) or not all(isinstance(m, str) for m in modes):
            add("error", field, f"'{field}' must be a list of MIME type strings.")
        elif not modes:
            add("warn", field, f"'{field}' is empty — clients cannot tell what content the agent accepts/produces.")

    # --- skills ------------------------------------------------------------
    skills = card.get("skills")
    if skills is None:
        add("error", "skills", "Required field 'skills' is missing.",
            "Declare at least one skill; skills are how clients decide whether to delegate to you.")
    elif not isinstance(skills, list):
        add("error", "skills", "'skills' must be a list.")
    elif not skills:
        add("warn", "skills", "Agent declares no skills — it is invisible to skill-based discovery.")
    else:
        for i, skill in enumerate(skills):
            where = f"skills[{i}]"
            if not isinstance(skill, dict):
                add("error", where, "Each skill must be an object.")
                continue
            for field in ("id", "name", "description"):
                if not isinstance(skill.get(field), str) or not skill.get(field, "").strip():
                    add("error", f"{where}.{field}", f"Skill is missing required '{field}'.")
            if not isinstance(skill.get("tags"), list) or not skill.get("tags"):
                add("warn", f"{where}.tags", "Skill has no tags.",
                    "Tags power discovery/matchmaking — add a few, e.g. [\"weather\"].")
            if not skill.get("examples"):
                add("info", f"{where}.examples", "No examples given.",
                    "Example prompts help other agents (and their LLMs) phrase requests correctly.")

    # --- security ----------------------------------------------------------
    schemes = card.get("securitySchemes")
    if schemes is not None:
        if not isinstance(schemes, dict):
            add("error", "securitySchemes", "'securitySchemes' must be an object mapping names to schemes.")
            schemes = {}
        else:
            for name, scheme in schemes.items():
                where = f"securitySchemes.{name}"
                if not isinstance(scheme, dict):
                    add("error", where, "Each security scheme must be an object.")
                elif scheme.get("type") not in KNOWN_SECURITY_TYPES:
                    add("error", where,
                        f"Unknown security scheme type '{scheme.get('type')}'.",
                        f"Expected one of: {', '.join(sorted(KNOWN_SECURITY_TYPES))}.")

    security = card.get("security")
    if security is not None:
        if not isinstance(security, list):
            add("error", "security", "'security' must be a list of requirement objects.")
        else:
            declared = set(schemes.keys()) if isinstance(schemes, dict) else set()
            for i, requirement in enumerate(security):
                if not isinstance(requirement, dict):
                    add("error", f"security[{i}]", "Each security requirement must be an object.")
                    continue
                for ref in requirement:
                    if ref not in declared:
                        add("error", f"security[{i}].{ref}",
                            f"References scheme '{ref}' which is not defined in securitySchemes.",
                            "Every requirement must point at a declared scheme.")

    if not card.get("securitySchemes") and not card.get("security"):
        add("info", "securitySchemes",
            "Card declares no authentication — anyone can call this agent.",
            "Fine for open/demo agents; declare schemes (e.g. bearer) before exposing anything sensitive.")

    return findings


def summarize(findings: list[dict]) -> dict:
    errors = sum(1 for f in findings if f["level"] == "error")
    warnings = sum(1 for f in findings if f["level"] == "warn")
    if errors:
        grade = "F" if errors > 2 else "D"
    elif warnings > 2:
        grade = "C"
    elif warnings:
        grade = "B"
    else:
        grade = "A"
    return {"errors": errors, "warnings": warnings, "grade": grade}

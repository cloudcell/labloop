"""MCP surface probe — audit the agent-facing surface for clarity.

Grades how learnable the tool surface is for an agent, not correctness:

  Phase A (static): lint each inputSchema — undocumented params,
                    hidden closed-vocab strings, object|string unions
                    with no shape docs, unstructured outputs.
  Phase B (reads):  call read-only tools for real; probe get_* with a
                    sentinel ID to grade not-found error quality.
  Phase C (writes): call mutating tools with {} only — a guaranteed
                    schema-validation failure before any handler runs —
                    to grade whether the rejection teaches correct use.
                    No payload that could succeed is ever sent.

Safety invariant: no call can mutate state. Read tools are called with
valid args; everything else gets an empty-arg validation probe or a
sentinel ID that cannot resolve.

Transport-agnostic: probe_client works with any client exposing
list_tools/list_resources/read_resource/call_tool — the in-process
mcp.client.Client (tests) and ClientSession over streamable HTTP
(live-stack audit in scripts/mcp_probe.py) both qualify.
"""

from __future__ import annotations

import json

SENTINEL = "probe-00000000"  # an ID that cannot exist in any store

# Verb classification by name prefix — determines the probe we send.
READ_PREFIXES = ("list_", "get_", "check_", "verify_", "assess_")

# Zero-required-arg tools that mutate despite looking safe — never call.
# (Empty as of the W4 guard work: archive_pending_programmes,
# capture_pending_artifacts and refresh_roster now require dry_run, so
# {} is schema-rejected; register_challenger rejects the empty form at
# handler level. All four are probed like normal tools — their {}
# rejection doubles as a regression check on the guards.)
UNGUARDED_MUTATIONS: set[str] = set()

# Params whose names imply a closed vocabulary. If the schema types them
# as bare "string" with no enum, the valid values are undiscoverable.
HIDDEN_ENUM_HINTS = {
    "verdict", "status", "to_status", "metric_direction", "arm",
    "source", "tool", "relation", "ref_type", "type", "split",
    "regime", "scope", "policy", "context_type", "campaign_arm",
}


def is_read_tool(name: str) -> bool:
    return name.startswith(READ_PREFIXES)


def lint_schema(tool: dict) -> list[str]:
    """Static clarity findings on one tool's schemas."""
    issues = []
    desc = tool.get("description") or ""
    schema = tool.get("input_schema") or tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    for pname, pspec in props.items():
        if not isinstance(pspec, dict):
            continue
        if "description" not in pspec:
            issues.append(f"param `{pname}` has no description")
        if pname in HIDDEN_ENUM_HINTS:
            variants = [pspec] if "anyOf" not in pspec else pspec["anyOf"]
            is_bare_string = any(
                v.get("type") == "string" for v in variants
                if isinstance(v, dict)
            )
            has_enum = any(
                "enum" in v for v in variants if isinstance(v, dict)
            )
            has_object = any(
                v.get("type") == "object" for v in variants
                if isinstance(v, dict)
            )
            # A dict|string union is an open object, not a closed vocab —
            # the name heuristic can't apply an enum to it.
            if is_bare_string and not has_enum and not has_object:
                documented = pname in desc or (
                    pname == "verdict" and "verdict" in desc.lower()
                )
                if not documented:
                    issues.append(
                        f"`{pname}` is a bare string but looks like a "
                        "closed vocab — valid values undiscoverable"
                    )
                else:
                    issues.append(
                        f"`{pname}`: closed vocab described only in "
                        "prose, not as an enum"
                    )
        if "anyOf" in pspec:
            kinds = [
                v.get("type") for v in pspec["anyOf"]
                if isinstance(v, dict)
            ]
            if "object" in kinds and "string" in kinds:
                # Union is legal (JSON-string fallback for clients that
                # can't emit objects) — the defect is an *undocumented*
                # shape. A description carrying the field layout ({...})
                # or naming the JSON-encoded fallback counts as documented.
                d = pspec.get("description") or ""
                if "{" not in d and "json" not in d.lower():
                    issues.append(
                        f"`{pname}` accepts object|string union — object "
                        "shape undocumented"
                    )

    out = tool.get("output_schema") or tool.get("outputSchema") or {}
    out_props = out.get("properties") or {}
    if set(out_props.keys()) == {"result"}:
        issues.append("output is unstructured {result: string}")
    elif not out:
        # Post-W5a baseline: tools return structured_content but most
        # declare no output_schema yet — that's the W5b remainder,
        # counted not failed.
        issues.append("no declared output_schema")

    if required and not desc.strip():
        issues.append("no tool description at all")
    return issues


def grade_error(err_text: str, probe_kind: str) -> tuple[str, str]:
    """Grade a rejected call's error text for teaching quality."""
    low = err_text.lower()
    if probe_kind == "missing_args":
        if "required" in low or "missing" in low or "field" in low:
            return "CLEAR", "validation error names the missing input"
        return "UNCLEAR", "rejection does not name what was missing"
    if probe_kind == "sentinel_id":
        if "not found" in low or "no such" in low or "unknown" in low:
            return "CLEAR", "clean not-found error"
        if "traceback" in low or ("exception" in low
                                  and "not found" not in low):
            return "UNCLEAR", "internal error leaked instead of not-found"
        return "PARTIAL", (
            f"rejected, but message is ambiguous: {err_text[:120]}")
    return "PARTIAL", err_text[:120]


async def _call(client, tname: str, args: dict, kind: str) -> dict:
    try:
        result = await client.call_tool(tname, args)
        if getattr(result, "is_error",
                   getattr(result, "isError", False)):
            text = (result.content[0].text if result.content
                    and hasattr(result.content[0], "text")
                    else str(result.content))
            verdict, note = grade_error(text, kind)
            return {"kind": kind, "verdict": verdict, "note": note,
                    "raw": text[:300]}
        text = (result.content[0].text if result.content
                and hasattr(result.content[0], "text") else "")
        # Errors delivered inside a *successful* result payload — the
        # single biggest harness hazard: is_error=False, so an agent
        # must sniff the JSON body to know the call failed.
        payload_err = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "error" in parsed:
                # A `verified` key marks a verification *verdict* — the
                # call succeeded; `error` is a report field, not a
                # smuggled failure (verify_archive/verify_data). On a
                # sentinel probe, verified:false IS the correct
                # response: the thing does not verify.
                if "verified" in parsed:
                    if kind == "sentinel_id":
                        return {"kind": kind, "verdict": "CLEAR",
                                "note": "verification verdict — "
                                        "verified:false, error names "
                                        "the reason",
                                "raw": text[:300]}
                else:
                    payload_err = str(parsed["error"])
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if payload_err is not None:
            if kind == "sentinel_id":
                if "not found" in payload_err.lower():
                    return {"kind": kind, "verdict": "PARTIAL",
                            "note": "clean not-found message, BUT "
                                    "delivered as a successful result "
                                    "— error channel unused",
                            "raw": text[:300]}
                return {"kind": kind, "verdict": "PARTIAL",
                        "note": f"error-as-result: {payload_err[:100]}",
                        "raw": text[:300]}
            return {"kind": kind, "verdict": "PARTIAL",
                    "note": f"error-as-result: {payload_err[:100]}",
                    "raw": text[:300]}
        if kind == "live_read":
            if parsed is not None:
                structured = getattr(result, "structured_content", None)
                if structured is not None and structured == parsed:
                    return {"kind": kind, "verdict": "CLEAR",
                            "structured": True,
                            "note": f"returned JSON + structured_content "
                                    f"({len(text)}B)"}
                return {"kind": kind, "verdict": "PARTIAL",
                        "structured": False,
                        "note": "parseable JSON but no matching "
                                "structured_content — output not "
                                "machine-readable without parsing"}
            return {"kind": kind, "verdict": "PARTIAL",
                    "note": f"returned non-JSON text "
                            f"({len(text)}B) — must be parsed "
                            "by hand"}
        if kind == "sentinel_id":
            return {"kind": kind, "verdict": "UNCLEAR",
                    "note": "sentinel ID returned a SUCCESS payload — "
                            "no not-found signal at all",
                    "raw": text[:300]}
        # a write tool succeeded on {} — that means it mutated!
        return {"kind": kind, "verdict": "UNCLEAR",
                "note": "SUCCEEDED on empty args — unguarded mutation",
                "raw": text[:300]}
    except Exception as e:
        text = str(e)
        verdict, note = grade_error(text, kind)
        return {"kind": kind, "verdict": verdict, "note": note,
                "raw": text[:300]}


async def probe_client(client, server_name: str) -> dict:
    """Probe one server's surface through an already-initialized
    client. Same report shape the live-stack script emits."""
    report = {"server": server_name, "tools": [], "resources": [],
              "unreachable": False}
    try:
        tools_result = await client.list_tools()
        tools = [t.model_dump() for t in tools_result.tools]

        # ── resources: read each ──
        try:
            res = await client.list_resources()
            for r in res.resources:
                entry = {"uri": str(r.uri), "name": r.name}
                try:
                    content = await client.read_resource(r.uri)
                    text = (content.contents[0].text
                            if content.contents else "")
                    entry["status"] = "CLEAR"
                    entry["bytes"] = len(text)
                except Exception as e:
                    entry["status"] = "UNCLEAR"
                    entry["error"] = str(e)[:200]
                report["resources"].append(entry)
        except Exception as e:
            report["resources"].append(
                {"uri": "(list_resources)", "status": "UNCLEAR",
                 "error": str(e)[:200]})

        # ── per-tool probes ──
        for tool in tools:
            tname = tool["name"]
            schema = (tool.get("input_schema")
                      or tool.get("inputSchema") or {})
            required = schema.get("required") or []
            entry = {
                "tool": tname,
                "lint": lint_schema(tool),
                "probes": [],
            }

            if tname in UNGUARDED_MUTATIONS:
                entry["probes"].append({
                    "kind": "skipped",
                    "verdict": "UNCLEAR",
                    "note": "zero required args but mutates — "
                            "nothing stops an accidental call",
                })
            elif is_read_tool(tname):
                if required:
                    # ID-bearing read: sentinel probe
                    args = {p: SENTINEL for p in required}
                    entry["probes"].append(
                        await _call(client, tname, args, "sentinel_id"))
                else:
                    # pure read: real call
                    entry["probes"].append(
                        await _call(client, tname, {}, "live_read"))
            else:
                # write tool: empty-args validation probe only
                entry["probes"].append(
                    await _call(client, tname, {}, "missing_args"))

            report["tools"].append(entry)
    except Exception as e:
        report["unreachable"] = True
        report["error"] = str(e)[:300]
    return report


def worst_verdict(tool_entry: dict) -> str:
    """Fold a tool's probes + lint into its single worst verdict."""
    worst = "CLEAR"
    for p in tool_entry["probes"]:
        v = p["verdict"]
        if v == "UNCLEAR":
            return "UNCLEAR"
        if v == "PARTIAL":
            worst = "PARTIAL"
    if tool_entry["lint"] and worst == "CLEAR":
        worst = "PARTIAL"
    return worst


def summarize(reports: list[dict]) -> str:
    lines = ["# MCP surface probe — clarity audit", ""]
    for rep in reports:
        srv = rep["server"]
        if rep.get("unreachable"):
            lines.append(f"## {srv} — UNREACHABLE: {rep.get('error')}")
            continue
        n_tools = len(rep["tools"])
        n_res = len(rep["resources"])
        counts = {"CLEAR": 0, "PARTIAL": 0, "UNCLEAR": 0}
        rows = []
        for t in rep["tools"]:
            worst = worst_verdict(t)
            counts[worst] += 1
            notes = [f"{p['kind']}: {p['note']}" for p in t["probes"]]
            notes += [f"lint: {i}" for i in t["lint"]]
            rows.append((t["tool"], worst, notes))
        lines.append(
            f"## {srv} — {n_tools} tools, {n_res} resources "
            f"| CLEAR {counts['CLEAR']} / PARTIAL {counts['PARTIAL']} "
            f"/ UNCLEAR {counts['UNCLEAR']}")
        lines.append("")
        for r in rep["resources"]:
            lines.append(
                f"- RES `{r['uri']}` [{r.get('status')}] "
                + (r.get("error") or f"{r.get('bytes', 0)}B"))
        for tname, verdict, notes in rows:
            lines.append(f"- **{tname}** [{verdict}]")
            for n in notes:
                lines.append(f"    - {n}")
        lines.append("")
    return "\n".join(lines)

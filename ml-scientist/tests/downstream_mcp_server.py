"""A minimal MCP server implementing the optimizer/executor/claims role
interfaces. Used for integration testing.

Run as: python downstream_mcp_server.py --role optimizer
"""

import argparse
import asyncio
import json

from mcp.server.mcpserver import MCPServer


def create_optimizer_server():
    mcp = MCPServer("test-optimizer", "0.1.0")
    studies = {}
    results = {}
    ask_count = 0

    @mcp.tool()
    def create_study(programme_id: str, variables: list, direction: str = "maximize") -> str:
        studies[programme_id] = variables
        results[programme_id] = []
        return f"study-{programme_id}"

    @mcp.tool()
    def ask(programme_id: str) -> str:
        nonlocal ask_count
        ask_count += 1
        variables = studies.get(programme_id, ["learning_rate"])
        return json.dumps({v: 0.01 * ask_count for v in variables})

    @mcp.tool()
    def tell(programme_id: str, trial_id: str, result: dict) -> str:
        results.setdefault(programme_id, []).append(result)
        return "ok"

    @mcp.tool()
    def best_trials(programme_id: str) -> str:
        r = results.get(programme_id, [])
        if not r:
            return json.dumps([])
        best = max(r, key=lambda x: list(x.values())[0] if x else 0)
        return json.dumps([{"config": {}, "result": best}])

    @mcp.tool()
    def param_importance(programme_id: str) -> str:
        variables = studies.get(programme_id, ["learning_rate"])
        return json.dumps({v: 1.0 / len(variables) for v in variables})

    return mcp


def create_executor_server():
    mcp = MCPServer("test-executor", "0.1.0")

    @mcp.tool()
    def execute_code(code: str) -> str:
        return json.dumps({"status": "completed", "output": "integration test execution"})

    @mcp.tool()
    def read_cell_output(cell_id: str) -> str:
        return json.dumps({"cell_id": cell_id, "output": "integration test output"})

    return mcp


def create_claims_server():
    mcp = MCPServer("test-claims", "0.1.0")
    claims = {}
    edges = {}

    @mcp.tool()
    def assert_claim(
        content: str,
        type: str,
        confidence: float,
        evidence: list | None = None,
        source_id: str | None = None,
    ) -> str:
        for cid, c in claims.items():
            if c["content"] == content and c["type"] == type:
                return json.dumps({"claim_id": cid, "deduplicated": True, "status": "exists"})
        claim_id = f"claim-{len(claims) + 1}"
        claims[claim_id] = {
            "id": claim_id, "content": content, "type": type,
            "confidence": confidence, "evidence": evidence or [],
            "source_id": source_id,
        }
        return json.dumps({"claim_id": claim_id, "status": "created"})

    @mcp.tool()
    def relate(from_claim: str, to_ref: str, ref_type: str, relation: str) -> str:
        edge_id = f"edge-{len(edges) + 1}"
        edges[edge_id] = {
            "id": edge_id, "from_claim": from_claim, "to_ref": to_ref,
            "ref_type": ref_type, "relation": relation,
        }
        return json.dumps({"edge_id": edge_id, "status": "created"})

    @mcp.tool()
    def get_claim(claim_id: str) -> str:
        return json.dumps(claims.get(claim_id, {"error": "not found"}))

    @mcp.tool()
    def list_claims(type: str | None = None, limit: int = 50) -> str:
        out = [c for c in claims.values() if type is None or c["type"] == type]
        return json.dumps({"claims": out[:limit], "total": len(out)})

    return mcp


def create_evidence_server():
    """A stub of the Loop-0 read surface, for zetesis pull tests.

    Serves canned payloads containing real-shaped entity ids so the
    pull_evidence ref-extraction path can be exercised end-to-end.
    """
    mcp = MCPServer("test-evidence", "0.1.0")

    @mcp.tool()
    def list_active_programmes() -> str:
        return json.dumps({
            "programmes": [
                {"id": "prog-aaaa1111", "goal": "study X"},
                {"id": "prog-bbbb2222", "goal": "study Y"},
            ],
            "total": 2,
        })

    @mcp.tool()
    def list_trials(programme_id: str) -> str:
        return json.dumps({
            "trials": [
                {"id": "trial-cccc3333", "status": "completed"},
                {"id": "trial-dddd4444", "status": "failed"},
            ],
        })

    @mcp.tool()
    def list_hypotheses(programme_id: str) -> str:
        return json.dumps({
            "hypotheses": [{"id": "hyp-eeee5555", "statement": "h"}],
        })

    @mcp.tool()
    def get_trial_status(programme_id: str, trial_id: str) -> str:
        return json.dumps({"trial_id": trial_id, "status": "completed"})

    @mcp.tool()
    def assess_programme(programme_id: str) -> str:
        return json.dumps({
            "programme_id": programme_id,
            "conclusions": [{"id": "conc-ffff6666"}],
            "observations": [{"id": "obs-aaaa7777"}],
        })

    @mcp.tool()
    def get_candidate_lineage(candidate_id: str) -> str:
        return json.dumps({"lineage": [{"id": candidate_id}]})

    @mcp.tool()
    def list_archives() -> str:
        return json.dumps({"archives": []})

    @mcp.tool()
    def get_archive(archive_id: str) -> str:
        return json.dumps({"archive_id": archive_id})

    @mcp.tool()
    def get_archived_programme(programme_id: str) -> str:
        return json.dumps({"programme_id": programme_id})

    @mcp.tool()
    def describe_blob(content_hash: str) -> str:
        return json.dumps({
            "exists": True,
            "resolved_in": ["artifact_files"],
            "size_bytes": 4,
        })

    return mcp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["optimizer", "executor", "claims", "evidence"], required=True)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    if args.role == "optimizer":
        mcp = create_optimizer_server()
    elif args.role == "executor":
        mcp = create_executor_server()
    elif args.role == "evidence":
        mcp = create_evidence_server()
    else:
        mcp = create_claims_server()

    if args.transport == "http":
        asyncio.run(mcp.run_streamable_http_async(
            host="127.0.0.1",
            port=args.port,
            stateless_http=True,
        ))
    else:
        asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()

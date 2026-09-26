"""Session protocol resource — improver://session.

The read-at-session-start resource for driving clients: the enforced
Loop-2 lifecycle steps, the current champion, open tournaments, and
the boundary rules. One loop up from zetesis — this loop improves
the improver; it never runs experiments or investigations itself.
"""

from __future__ import annotations

import json

from ..state.store import ImproverStore
from .status import status_digest

LOOP_STEPS = [
    {
        "step": 1,
        "name": "register_improver",
        "tool": "register_improver",
        "description": (
            "Record an improver version. Genesis takes no parent; a "
            "child names its parent and optionally the admitted/"
            "conditional proposal it implements. The first improver "
            "bootstraps as champion; afterwards the pointer moves "
            "only through promote_policy/rollback. Artifact digests "
            "are verified claims: each must resolve to a blob "
            "ingested on episteme (checked through the loop0 "
            "channel) or be 'none' — an unwired loop0 refuses "
            "digests, never skips the check."
        ),
        "inputs": ["code_artifact_digest", "model_ref",
                   "capability_profile", "parent_id?", "proposal_id?"],
        "outputs": ["improver_id"],
    },
    {
        "step": 2,
        "name": "create_meta_contract",
        "tool": "create_meta_contract",
        "description": (
            "Mint a versioned evaluation contract. metrics must "
            "declare primary_metric — the quantity recursive_gain is "
            "computed over. Contracts freeze at first open_tournament."
        ),
        "inputs": ["metrics", "promotion_policy", "holdouts?",
                   "budget?"],
        "outputs": ["contract_id", "version"],
    },
    {
        "step": 3,
        "name": "propose_meta_change",
        "tool": "propose_meta_change",
        "description": (
            "Submit a typed meta-change to the ADR-0003 admission "
            "gate. class_map names every touched component; the "
            "kernel classifies authoritatively. Class-1 touch → "
            "rejected (stored); class-3/unknown → conditional "
            "(human gate at promotion); else admitted."
        ),
        "inputs": ["proposer_improver_id", "spec_delta", "class_map",
                   "expected_benefit", "falsification",
                   "rollback_plan", "budget?"],
        "outputs": ["proposal_id", "status"],
    },
    {
        "step": 4,
        "name": "open_tournament",
        "tool": "open_tournament",
        "description": (
            "Open the paired ancestral comparison. One budget B "
            "governs both arms — equal spend is structural. The "
            "candidate must descend from the parent. The server "
            "records; the driving client runs descendant evaluations "
            "through the lower loops."
        ),
        "inputs": ["contract_id", "parent_improver_id",
                   "candidate_improver_id", "budget", "seeds?"],
        "outputs": ["tournament_id"],
    },
    {
        "step": 4.5,
        "name": "pull_evidence",
        "tool": "pull_evidence",
        "description": (
            "Read upstream evidence (source: loop0|loop1|anamnesis; "
            "whitelisted tools only) inside a live proposal or "
            "tournament context. Every pull is logged — decisions "
            "cite these refs."
        ),
        "inputs": ["context_type", "context_id", "source", "tool",
                   "args?"],
        "outputs": ["evidence_ref_id", "ref_ids", "result"],
        "repeat": "As often as the decision requires",
    },
    {
        "step": 5,
        "name": "record_tournament_result",
        "tool": "record_tournament_result",
        "description": (
            "Append what one arm produced — insert-only. metrics "
            "must carry the contract's primary_metric; 'seed' (a "
            "scalar — one row per seed) groups per-seed bests for "
            "E[best descendant]. Malformed rows are repairable via "
            "correct_tournament_result while the tournament is open."
        ),
        "inputs": ["tournament_id", "arm", "descendant_spec",
                   "metrics"],
        "outputs": ["result_id"],
        "repeat": "Per descendant evaluation, per arm",
    },
    {
        "step": 6,
        "name": "close_tournament",
        "tool": "close_tournament",
        "description": (
            "Seal the record and compute recursive_gain — the "
            "per-seed-best ratio, direction-normalized so >1 always "
            "means the candidate produces better descendants. "
            "Both arms need ≥1 result — an unpaired tournament "
            "refuses to close."
        ),
        "inputs": ["tournament_id"],
        "outputs": ["recursive_gain"],
    },
    {
        "step": 6.5,
        "name": "void_tournament",
        "tool": "void_tournament",
        "description": (
            "The dead-record exit — open → voided for tournaments "
            "that were opened but can never honestly close (no "
            "paired results coming). Terminal; computes no "
            "recursive_gain. Refuses paired tournaments — those "
            "close. Rationale and decided_by are recorded on the "
            "row."
        ),
        "inputs": ["tournament_id", "rationale", "decided_by"],
        "outputs": ["voided_at"],
    },
    {
        "step": 7,
        "name": "record_meta_decision",
        "tool": "record_meta_decision",
        "description": (
            "The governed verdict — insert-only. Requires ≥1 "
            "evidence_ref, non-empty rationale, attributable "
            "decided_by ('human:<name>' required for conditional "
            "proposals). Mints a methodological claim to anamnesis "
            "with derived_from edges."
        ),
        "inputs": ["candidate_improver_id", "verdict",
                   "evidence_refs", "rationale", "decided_by",
                   "contract_id?", "tournament_id?"],
        "outputs": ["decision_id", "claim_id?"],
    },
    {
        "step": 8,
        "name": "promote_policy / rollback",
        "tool": "promote_policy",
        "description": (
            "promote_policy mints the policy_version and moves the "
            "champion pointer — requires the candidate's latest "
            "decision to be 'promote'. rollback records a decision "
            "and restores the displaced champion. Reversal is an "
            "event, never an edit."
        ),
        "inputs": ["candidate_improver_id", "policy (promote)",
                   "rationale+decided_by+evidence_refs (rollback)"],
        "outputs": ["policy_version_id", "restored_champion"],
    },
]

READ_ONLY_NOTE = (
    "Arete is read-only with respect to the lower loops: "
    "pull_evidence enforces per-source tool whitelists, the adaptors "
    "expose no write methods, and descendant execution belongs to "
    "Loop 0/1. To act on a promoted policy, drive the lower loops' "
    "own tools directly — commands flow down, evidence flows up."
)

SCAFFOLD_NOTE = (
    "Scaffold status: the lifecycle is enforced end-to-end, but the "
    "Loop-1 population substrate (candidate versions, promotion "
    "outcomes) is not yet populated upstream. Tournaments record the "
    "protocol shape honestly — with no real descendant evaluations, "
    "they have no results and refuse to close rather than fabricate "
    "a comparison."
)


def _get_state_summary(store: ImproverStore) -> dict:
    champion = store.get_champion()
    open_tournaments, _ = store.list_tournaments(status="open", limit=50)
    return {
        "champion": (
            {
                "id": champion.id,
                "model_ref": champion.model_ref,
                "parent_id": champion.parent_id,
            }
            if champion else None
        ),
        "open_tournaments": [
            {
                "id": t.id,
                "parent": t.parent_improver_id,
                "candidate": t.candidate_improver_id,
                "created_at": t.created_at,
            }
            for t in open_tournaments
        ],
    }


def register(mcp, store: ImproverStore, adaptors=None) -> None:
    """Register the session protocol resource."""

    @mcp.resource("improver://session")
    def get_session_protocol() -> str:
        """The arete session protocol — read at session start."""
        payload = {
            "server": "ml-arete-mcp",
            "loop": 2,
            "name": "recursive loop — improves the improver",
            "steps": LOOP_STEPS,
            "note": (
                "Read this resource at the start of a session to "
                "understand the loop and where you are in it. A pull "
                "is not a result, a result is not a verdict, and a "
                "verdict is not a promotion — each stage is a "
                "durable, attributable record."
            ),
            "read_only_boundary": READ_ONLY_NOTE,
            "scaffold_status": SCAFFOLD_NOTE,
            "state": _get_state_summary(store),
            # The actionable digest — same payload as
            # improver://status, embedded so the recovery resource
            # stays self-sufficient.
            "status": status_digest(store, adaptors),
        }
        return json.dumps(payload, indent=2)

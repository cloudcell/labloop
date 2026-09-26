"""Session protocol resource — protocol://session.

This resource provides the agent with everything it needs to recover
loop context after a client compaction or session restart:

1. The loop steps (the scientific method as a tool sequence)
2. The tool catalog (what tools are available)
3. The state machine (legal transitions for each entity)
4. Dynamic current state (active programmes, hypotheses, trials)

The agent reads this resource at the start of a session to understand
where it is in the loop and what to do next.
"""

from __future__ import annotations

import json

from ..state.store import StateStore
from .status import status_digest


# --- Static protocol content (the loop, tools, state machine) ---

LOOP_STEPS = [
    {
        "step": 1,
        "name": "create_programme",
        "tool": "create_programme",
        "description": "Create a research programme with goal, constraints, budget.",
        "inputs": ["goal", "constraints", "allowed_variables", "budget_max_trials", "budget_max_wall_time_hours"],
        "outputs": ["programme_id"],
    },
    {
        "step": 2,
        "name": "formulate_hypothesis",
        "tool": "formulate_hypothesis",
        "description": "State a falsifiable hypothesis and its failure criterion.",
        "inputs": ["programme_id", "statement", "failure_criterion", "variables_involved"],
        "outputs": ["hypothesis_id"],
    },
    {
        "step": 3,
        "name": "prepare_data",
        "tool": "prepare_data",
        "description": "Prepare datasets (generated or captured) for train/validation/test splits.",
        "inputs": ["split", "regime", "generator_code_ref|source_uri", "generator_seed|version"],
        "outputs": ["data_ref_id"],
        "repeat": "Once per split (train, validation, test)",
    },
    {
        "step": 4,
        "name": "design_experiment",
        "tool": "design_experiment",
        "description": "Design a trial (configuration) within the programme.",
        "inputs": ["programme_id", "hypothesis_id", "config"],
        "outputs": ["trial_id"],
    },
    {
        "step": 5,
        "name": "capture_bundle",
        "tool": "capture_bundle",
        "description": (
            "Seal the auxiliary bundle (code, env, seeds, data_refs) — "
            "pre-registration: auxiliaries are fixed BEFORE execution so "
            "they can't be retro-fitted to results. Rejected on trials "
            "that have left 'designed'; run_trial requires it. What "
            "actually ran is captured separately at finalization "
            "(executed_code.json artifact)."
        ),
        "inputs": ["trial_id", "code_ref", "env_ref", "seeds", "splits", "data_refs?"],
        "outputs": ["bundle_id"],
    },
    {
        "step": 6,
        "name": "run_trial",
        "tool": "run_trial",
        "description": (
            "Run the trial. Resolves DataRefs to read-only paths. "
            "While running, poll get_trial_status adaptively — the "
            "reported eta_seconds drives the interval (~eta/4), never "
            "a blind fixed sleep; see the monitor_running_trial prompt."
        ),
        "inputs": ["programme_id", "trial_id"],
        "outputs": ["status (running|completed|failed)"],
    },
    {
        "step": 7,
        "name": "record_observation",
        "tool": "record_observation",
        "description": "Record the metrics and variance from the trial.",
        "inputs": ["trial_id", "metrics", "variance", "spatiotemporal_region"],
        "outputs": ["observation_id"],
    },
    {
        "step": 8,
        "name": "update_belief",
        "tool": "update_belief",
        "description": "Update the belief state (posterior over the objective).",
        "inputs": ["programme_id", "trial_id", "observation_id"],
        "outputs": ["belief_id"],
    },
    {
        "step": 9,
        "name": "conclude_hypothesis",
        "tool": "conclude_hypothesis",
        "description": "Accept, reject, or mark the hypothesis inconclusive.",
        "inputs": ["programme_id", "hypothesis_id", "verdict", "evidence_summary"],
        "outputs": ["conclusion_id"],
    },
    {
        "step": 10,
        "name": "close_programme",
        "tool": "close_programme",
        "description": "Close the programme when the budget is exhausted or goal met.",
        "inputs": ["programme_id", "status (completed|abandoned)"],
        "outputs": ["status (completed|abandoned)", "archived"],
    },
]

# Honest encodings for outcomes that are not clean measurements — the
# enforcement layer rejects attempts to launder failures into evidence.
FAILURE_HANDLING = [
    "A 'failed' trial produced no measurement: record_observation "
    "rejects it (status must be 'completed'). The failure is already "
    "recorded — trial status, executor_output (stderr), artifacts.",
    "'completed' requires an executor record: the store refuses to "
    "write it while executor_output_json carries no execution verdict "
    "(exit_code/status/stdout/...). An executor that returns nothing "
    "recordable finalizes as 'failed', never 'completed'.",
    "All-zero variance is rejected: n identical outcomes are one "
    "effective measurement (a point estimate), not reproducibility. "
    "For genuinely identical results, report per-seed values in "
    "metrics or an SE estimate — never variance={...: 0}.",
    "Do not report variance on a different metric (e.g. runtime) to "
    "pass the gate while the primary metric was never measured.",
    "mark_retryable is for infrastructure faults only (lost output, "
    "server restart, timeout) — never for deterministic code crashes.",
    "Identical deterministic failure is methodological knowledge, not "
    "hypothesis evidence: mint it in anamnesis via assert_claim with "
    "tested_by→the failed trials.",
    "If no hypothesis can be concluded, close_programme(status="
    "'abandoned') is the honest verdict — the record truthfully says "
    "the direction produced no evidence.",
]

TOOL_CATALOG = [
    # Programme
    {"name": "create_programme", "category": "programme", "description": "Create a research programme"},
    {"name": "list_active_programmes", "category": "programme", "description": "List active programmes (check before creating)"},
    {"name": "close_programme", "category": "programme", "description": "Close a programme (auto-archives)"},
    {"name": "update_metric_direction", "category": "programme", "description": "Set minimize/maximize"},
    # Hypothesis
    {"name": "formulate_hypothesis", "category": "hypothesis", "description": "State a falsifiable hypothesis"},
    {"name": "list_hypotheses", "category": "hypothesis", "description": "List a programme's hypotheses (id recovery)"},
    {"name": "conclude_hypothesis", "category": "hypothesis", "description": "Accept/reject/inconclusive; mints a claim"},
    # Data
    {"name": "prepare_data", "category": "data", "description": "Prepare a dataset (generated or captured)"},
    {"name": "verify_data", "category": "data", "description": "Verify data provenance by re-computing hash"},
    # Trial
    {"name": "design_experiment", "category": "trial", "description": "Design a trial (configuration)"},
    {"name": "capture_bundle", "category": "trial", "description": "Capture the auxiliary bundle"},
    {"name": "capture_bundle_from_code_hash", "category": "trial", "description": "Capture a bundle by content hash"},
    {"name": "run_trial", "category": "trial", "description": "Run a trial"},
    {"name": "get_trial_status", "category": "trial", "description": "Check trial status"},
    {"name": "cancel_trial", "category": "trial", "description": "Cancel a running trial (failed) or abandon a designed one"},
    {"name": "mark_retryable", "category": "trial", "description": "Mark a trial as retryable (infra failure)"},
    {"name": "correct_trial_status", "category": "trial", "description": "Correct a mislabeled terminal trial (recorded repair — never hand-edit the DB)"},
    {"name": "wait_trial", "category": "trial", "description": "Wait server-side for a terminal trial status (≤60s — replaces the get_trial_status polling loop)"},
    {"name": "list_trials", "category": "trial", "description": "List a programme's trials (id recovery)"},
    # Observation
    {"name": "record_observation", "category": "observation", "description": "Record metrics and variance"},
    # Belief
    {"name": "update_belief", "category": "belief", "description": "Update the belief state"},
    # Assessment
    {"name": "assess_programme", "category": "assessment", "description": "Assess progressive vs degenerating"},
    {"name": "get_next_experiment", "category": "assessment", "description": "Next config from optimizer + budget"},
    # Candidate (RSI Phase 0 — researcher lineage)
    {"name": "register_candidate", "category": "candidate", "description": "Register a researcher version with parent lineage"},
    {"name": "get_candidate_lineage", "category": "candidate", "description": "Walk a candidate's parent chain to genesis"},
    {"name": "create_evaluation_contract", "category": "candidate", "description": "Create a versioned evaluation contract for a programme"},
    {"name": "record_promotion_decision", "category": "candidate", "description": "Record an attributed verdict on a candidate"},
    # Candidate read surface (Loop-1 population reads)
    {"name": "list_candidates", "category": "candidate", "description": "Enumerate the candidate population, newest first"},
    {"name": "get_candidate", "category": "candidate", "description": "Read one candidate version"},
    {"name": "list_promotion_decisions", "category": "candidate", "description": "A candidate's insert-only verdict trail"},
    {"name": "get_incumbent", "category": "candidate", "description": "Derive the incumbent from the decision trail"},
    {"name": "get_candidate_scorecard", "category": "candidate", "description": "Descendant quality over attributed programmes"},
    {"name": "get_evaluation_contract", "category": "candidate", "description": "Read one evaluation contract"},
    # Programme listing (general paginated read, attribution filter)
    {"name": "list_programmes", "category": "programme", "description": "Paginated programme listing with candidate/status filters"},
    # Archive
    {"name": "archive_pending_programmes", "category": "archive", "description": "Archive terminal programmes in the live DB"},
    {"name": "capture_pending_artifacts", "category": "archive", "description": "Capture artifacts for pre-capture trials"},
    {"name": "list_archives", "category": "archive", "description": "List archive files"},
    {"name": "get_archive", "category": "archive", "description": "Archive details incl. programmes"},
    {"name": "get_archived_programme", "category": "archive", "description": "An archived programme's details"},
    {"name": "verify_archive", "category": "archive", "description": "Verify archive integrity"},
    # Integrity — the invariant audit (report-only, logged per run)
    {"name": "check_invariants", "category": "integrity", "description": "Audit state.db against the loop's invariants"},
    {"name": "acknowledge_violation", "category": "integrity", "description": "Acknowledge an open violation (remediated | accepted-with-reason) — clears the write gate"},
    # Integrity — content-address resolution (read-only)
    {"name": "describe_blob", "category": "integrity", "description": "Resolve a content digest to held bytes across the blob stores"},
    {"name": "get_blob", "category": "integrity", "description": "Retrieve a blob's verified bytes by digest (base64)"},
]

STATE_MACHINE = {
    "programme": {
        "active": ["completed", "abandoned"],
        "completed": ["archived"],
        "abandoned": ["archived"],
        "archived": [],
        # closed programmes are immutable: every programme-scoped
        # mutation rejects (except cancel_trial/mark_retryable cleanup)
    },
    "hypothesis": {
        "proposed": ["under_test", "abandoned"],
        "under_test": ["accepted", "rejected", "inconclusive", "abandoned"],
        "accepted": [],
        "rejected": [],
        "inconclusive": [],
        "abandoned": [],
    },
    "trial": {
        "designed": ["running", "abandoned"],
        "running": ["completed", "failed", "retryable"],
        "completed": [],
        "failed": [],
        "retryable": [],
        "abandoned": [],
    },
}

RESOURCES = [
    {"uri": "executor://contract", "description": "The executor contract (run_training signature)"},
    {"uri": "programme://{id}", "description": "Programme details"},
    {"uri": "programme://{id}/hypotheses", "description": "Hypotheses in a programme"},
    {"uri": "programme://{id}/trials", "description": "Trials in a programme"},
    {"uri": "programme://{id}/belief", "description": "Current belief state"},
    {"uri": "programme://{id}/budget", "description": "Remaining budget"},
    {"uri": "trial://{id}/artifacts", "description": "Trial artifact manifest"},
    {"uri": "artifact://{content_hash}", "description": "Captured artifact file content"},
    {"uri": "dataref://{id}", "description": "DataRef details (provenance, hash, risk)"},
    {"uri": "code://{code_hash}", "description": "Captured code snippet (content-addressed source code)"},
    {"uri": "protocol://session", "description": "This resource — the session protocol"},
]

# Candidate attribution is orthogonal to the loop steps — it answers
# "which version of the researcher did this work", not "what step comes
# next". Presented as a contract, not a loop step.
def _candidate_attribution(required: bool) -> dict:
    """The attribution contract, phrased for the deployment's policy:
    advisory by default, mandatory under
    require_candidate_attribution."""
    purpose = (
        "Attribute your research to a registered candidate version so "
        "results are reproducible from the researcher's manifest "
        "(which model, code, and policy ran)."
    )
    purpose += (
        " REQUIRED on this deployment — create_programme rejects "
        "unattributed programmes." if required
        else " Optional but recommended."
    )
    return {
        "purpose": purpose,
        "required": required,
        "how": [
            "1. Call register_candidate once at session start (or reuse "
            "an existing candidate id — see get_candidate_lineage).",
            "2. Pass the returned candidate_id as candidate_version_id "
            "to create_programme. All hypotheses, trials, and "
            "observations inherit the attribution by join.",
            "3. create_evaluation_contract records the evaluation "
            "policy for a programme (versioned, insert-only).",
            "4. record_promotion_decision is for verdicts ON "
            "candidates (promote/reject/hold/rollback) — typically "
            "made by the operator, with decided_by attribution "
            "required.",
        ],
        "artifact_digests": (
            "code_artifact_digest and harness_artifact_digest are "
            "verified claims, not free strings: each must either "
            "resolve to a blob the server holds (check with "
            "describe_blob) or be the literal 'none'. If the version "
            "has code, ingest it first (PUT /artifacts or a capture "
            "tool) and pass the returned digest — ingest-first is "
            "the norm. Pass 'none' only when there is genuinely no "
            "artifact; never invent a hash — a well-formed digest "
            "of nothing is rejected."
        ),
    }


# The warmup obligation — what an agent owes the loop before
# scientific work. Presented as a contract, like CANDIDATE_ATTRIBUTION;
# the file-presence half lives in workspace/harness policy (no MCP
# server can observe the file), the enforceable residue is candidate
# attribution below.
WARMUP = {
    "obligation": (
        "Before scientific work: if the workspace has no agent-authored "
        "operating document (convention: AGENTICSCIENCE.md), do a dry "
        "run and write one. The document records environment facts you "
        "verified yourself, this loop grammar, and working rules "
        "learned from probes — updated as evidence accrues."
    ),
    "sequence": [
        "1. Read protocol://session and executor://contract.",
        "2. Probe read surfaces — list_* plus a sentinel get_* — to "
        "verify the environment, the is_error channel, and the "
        "closed-vocab enums exposed in tool schemas.",
        "3. Write the operating document from what you verified, not "
        "from assumption.",
        "4. Optionally content-address it (artifact ingest) and carry "
        "the returned digest on your register_candidate call — the "
        "document becomes part of the scientific record. The digest "
        "must resolve (describe_blob) or be 'none'; invented hashes "
        "are rejected.",
    ],
    "enforcement": (
        "Servers cannot observe workspace files — doc presence is "
        "workspace/harness policy. The server-side residue is "
        "candidate attribution: when require_candidate_attribution is "
        "set, create_programme rejects unattributed programmes."
    ),
}


STALE_PROGRAMME_HOURS = 24.0


def _get_dynamic_state(
    store: StateStore,
    stale_programme_hours: float = STALE_PROGRAMME_HOURS,
) -> dict:
    """Get the dynamic current state from the store."""
    # We can't list all programmes directly, but we can check for active ones
    # by querying the DB. For now, return a summary structure.
    import sqlite3
    from datetime import datetime, timezone
    from ..state.models import ProgrammeStatus

    conn = store.conn
    programmes = []
    try:
        rows = conn.execute(
            "SELECT id, goal, status, created_at FROM programmes WHERE status = 'active'"
        ).fetchall()
        for row in rows:
            prog_id = row["id"]
            trials = store.list_trials(prog_id)
            hypotheses = store.list_hypotheses(prog_id)
            active_hyps = [h for h in hypotheses if h.status.value == "under_test"]
            running_trials = [t for t in trials if t.status.value == "running"]
            completed_trials = [t for t in trials if t.status.value == "completed"]

            entry = {
                "id": prog_id,
                "goal": row["goal"],
                "status": row["status"],
                "hypotheses": {
                    "total": len(hypotheses),
                    "under_test": len(active_hyps),
                },
                "trials": {
                    "total": len(trials),
                    "running": len(running_trials),
                    "completed": len(completed_trials),
                },
            }

            # Staleness is surfaced, not enforced — an idle programme is
            # a fact for the agent to act on (resume or abandon), never
            # something the server silently closes.
            last_row = conn.execute(
                """
                SELECT MAX(ts) AS ts FROM (
                    SELECT created_at AS ts FROM programmes WHERE id = ?
                    UNION ALL SELECT created_at FROM hypotheses WHERE programme_id = ?
                    UNION ALL SELECT created_at FROM trials WHERE programme_id = ?
                    UNION ALL SELECT started_at FROM trials WHERE programme_id = ?
                    UNION ALL SELECT finished_at FROM trials WHERE programme_id = ?
                    UNION ALL SELECT o.created_at FROM observations o
                        JOIN trials t ON t.id = o.trial_id
                        WHERE t.programme_id = ?
                ) WHERE ts IS NOT NULL
                """,
                (prog_id,) * 6,
            ).fetchone()
            if last_row and last_row["ts"]:
                last = last_row["ts"]
                entry["last_activity_at"] = last
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                idle_h = (
                    datetime.now(timezone.utc) - last_dt
                ).total_seconds() / 3600
                entry["idle_hours"] = round(idle_h, 1)
                if idle_h > stale_programme_hours:
                    entry["stale"] = True
                    entry["hint"] = (
                        f"No activity for >{stale_programme_hours:g}h — "
                        "resume the loop or "
                        "close_programme(status='abandoned')."
                    )

            programmes.append(entry)
    except Exception:
        pass

    return {
        "active_programmes": programmes,
        "stale_threshold_hours": stale_programme_hours,
        "timestamp": _utc_now_iso(),
    }


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def register(
    mcp, store: StateStore,
    stale_programme_hours: float = STALE_PROGRAMME_HOURS,
    adaptor=None,
    require_candidate_attribution: bool = False,
) -> None:
    """Register the session protocol resource.

    stale_programme_hours is the [session] staleness policy — how long
    a programme may idle before the session resource marks it stale.
    require_candidate_attribution (same table) phrases the
    candidate_attribution section as mandatory vs advisory — the
    resource must tell the truth about the enforcement the tools apply.
    """

    @mcp.resource("protocol://session")
    def get_session_protocol() -> str:
        """The session protocol — everything the agent needs to recover
        loop context after a client compaction or session restart.

        Contains:
        1. The loop steps (the scientific method as a tool sequence)
        2. The tool catalog (what tools are available)
        3. The state machine (legal transitions)
        4. Resources (what URIs the agent can read)
        5. Dynamic current state (active programmes, hypotheses, trials)
        """
        protocol = {
            "version": "0.1.0",
            "description": (
                "The scientific experimentation loop. Each step produces "
                "evidence that feeds the next. The agent should read this "
                "resource at the start of a session to understand where "
                "it is in the loop and what to do next."
            ),
            "loop_steps": LOOP_STEPS,
            "tool_catalog": TOOL_CATALOG,
            "state_machine": STATE_MACHINE,
            "failure_handling": FAILURE_HANDLING,
            "warmup": WARMUP,
            "candidate_attribution": _candidate_attribution(
                require_candidate_attribution
            ),
            "resources": RESOURCES,
            "current_state": _get_dynamic_state(
                store, stale_programme_hours
            ),
            # The actionable digest — same payload as
            # protocol://status, embedded so the recovery resource
            # stays self-sufficient (one read after compaction).
            "status": status_digest(
                store, adaptor, stale_programme_hours
            ),
        }
        return json.dumps(protocol, indent=2)

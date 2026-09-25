"""MCP prompt templates for the arete recursive loop — meta-change
steering."""


def register(mcp) -> None:
    """Register prompt templates on the MCP server."""

    @mcp.prompt()
    def conduct_meta_change(candidate_improver_id: str) -> str:
        """How to run a meta-change through the governed lifecycle.

        The guidance a driving client should follow between
        propose_meta_change and promote_policy/rollback.
        """
        return (
            f"You are conducting a meta-change cycle concerning "
            f"candidate improver {candidate_improver_id}. Read "
            "improver://session for the loop state.\n\n"
            "Discipline (enforced where possible, expected where "
            "not):\n\n"
            "1. Classify every touched component BEFORE proposing. "
            "The admission gate reclassifies authoritatively — "
            "declaring a class-1 component as class-2 does not "
            "launder it; the proposal is rejected and the rejection "
            "is durable.\n"
            "2. A tournament is paired and ancestral. The candidate "
            "must descend from the parent arm, both arms run under "
            "the SAME budget B, and results must carry the "
            "contract's primary_metric. Judge improvers by the "
            "descendants they produce — never by the improver's own "
            "score.\n"
            "3. Prefer 'hold' over an underpowered tournament. If "
            "the arms produced too few descendants to estimate "
            "E[best], say so in the rationale — a weak comparison "
            "recorded honestly beats a confident promotion on "
            "noise.\n"
            "4. Pull contradicting evidence deliberately. A "
            "tournament that only records the candidate's wins is "
            "selection bias with extra steps.\n"
            "5. Every decision cites evidence_refs — pulls made "
            "inside the related proposal or tournament context. If "
            "you cannot ground the verdict, gather more evidence or "
            "decide 'hold'. Do not assert.\n"
            "6. Conditional proposals carry a human gate: promotion "
            "requires a promote decision whose decided_by starts "
            "'human:'. If no human reviewed, the honest verdict is "
            "'hold' — not a promotion signed by the improver "
            "itself.\n"
            "7. Rollback is a decision, not a deletion. The promote "
            "record stays; the rollback record supersedes it. The "
            "champion pointer restores to the displaced champion.\n"
            "8. If the Loop-1 substrate yields no real descendant "
            "evaluations, the tournament cannot close — report the "
            "missing substrate honestly rather than fabricating "
            "results. The scaffold's empty state is declared, not "
            "a bug."
        )

    @mcp.prompt()
    def scope_meta_change(
        target_component: str, motivation: str = ""
    ) -> str:
        """Turn a change idea into an admissible proposal."""
        why = f"\nMotivation given: {motivation}\n" if motivation else ""
        return (
            f"You are preparing a meta-change proposal.\n\n"
            f"Target component: {target_component}{why}\n\n"
            "Before calling propose_meta_change, make the proposal "
            "admissible:\n"
            "1. Name EVERY component the delta touches in class_map "
            "— unlisted components are unlisted only until the "
            "delta is read.\n"
            "2. Check the boundary class of each: class-1 "
            "(immutable: audit/provenance/permissions/budgets/"
            "promotion protocol/holdout access/rollback/"
            "enforcement kernel) → do not propose it; class-3 "
            "(evaluator, metric weighting, memory schema, "
            "scheduler) → expect conditional status and a human "
            "gate; class-2 → admit path.\n"
            "3. Write expected_benefit as a measurable claim about "
            "DESCENDANT quality (recursive gain), not about the "
            "improver itself.\n"
            "4. Write falsification as the observation that would "
            "kill the proposal — what result in the tournament "
            "refutes the expected benefit.\n"
            "5. Write rollback_plan concretely: which champion is "
            "restored and how the policy_version is retired.\n\n"
            "Then call propose_meta_change(proposer_improver_id=..., "
            "spec_delta={...}, class_map={...}, ...)."
        )

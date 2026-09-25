"""MCP prompt templates — scaffolds for common scientific workflows."""

from __future__ import annotations


def register(mcp) -> None:
    """Register prompt templates on the MCP server."""

    @mcp.prompt()
    def start_research_programme(
        goal: str, constraints: str, variables: str, budget_trials: int
    ) -> str:
        """Scaffold a research programme given a goal, constraints, and budget.

        Produces a create_programme call with typed fields.
        """
        return (
            f"You are starting a research programme.\n\n"
            f"Goal: {goal}\n"
            f"Constraints: {constraints}\n"
            f"Allowed variables: {variables}\n"
            f"Budget: {budget_trials} trials\n\n"
            f"Call create_programme with these parameters (typed, not strings):\n"
            f"- goal: \"{goal}\"\n"
            f"- constraints: {constraints}\n"
            f"- allowed_variables: {variables}\n"
            f"- budget: {{\"max_trials\": {budget_trials}, \"max_wall_time_hours\": 0}}\n\n"
            f"Then formulate a falsifiable hypothesis with a pre-declared failure criterion."
        )

    @mcp.prompt()
    def design_falsifiable_hypothesis(claim: str) -> str:
        """Given a claim, produce a hypothesis with a real failure criterion."""
        return (
            f"You are designing a falsifiable hypothesis.\n\n"
            f"Claim: {claim}\n\n"
            f"Before calling formulate_hypothesis, you must specify:\n"
            f"1. A failure_criterion — what observation would falsify this claim?\n"
            f"   (e.g., 'val_accuracy(X) <= val_accuracy(baseline) with p<0.05')\n"
            f"2. The variables_involved — which allowed variables does this claim vary?\n\n"
            f"A hypothesis without a failure criterion will be rejected (commitment 3:\n"
            f"Falsifiability is required for admission)."
        )

    @mcp.prompt()
    def review_programme_health(programme_id: str) -> str:
        """Given a programme ID, assess progressive vs degenerating."""
        return (
            f"You are reviewing the health of research programme {programme_id}.\n\n"
            f"Call assess_programme with programme_id=\"{programme_id}\".\n\n"
            f"Then interpret the result:\n"
            f"- 'progressive' means the programme is predicting novel facts\n"
            f"- 'degenerating' means it survives only by post-hoc patching\n"
            f"- 'insufficient-data' means more trials are needed\n\n"
            f"Read programme://{programme_id}/trials and programme://{programme_id}/belief\n"
            f"for details. If degenerating, recommend closing the programme."
        )

    @mcp.prompt()
    def monitor_running_trial(programme_id: str, trial_id: str) -> str:
        """How to poll a running trial — adaptive, ETA-driven, never a
        blind fixed sleep."""
        return (
            f"You are monitoring trial {trial_id} in programme {programme_id}.\n\n"
            f"Poll get_trial_status — but adaptively, driven by what it reports:\n\n"
            f"1. The response carries elapsed_seconds, progress "
            f"(the training code's own progress.json), and eta_seconds.\n"
            f"2. When eta_seconds is present, poll at roughly eta/4 — "
            f"frequent enough to catch early completion or failure, "
            f"sparse enough to not spam. NEVER sleep the full ETA: the "
            f"point of polling is that the ETA is a moving estimate.\n"
            f"3. When eta_seconds is null, poll at a moderate interval "
            f"(~30-60s) and watch elapsed_seconds + progress.pct "
            f"dynamics: a shrinking ETA or rising pct means healthy "
            f"progress; a stalling ETA or absent progress over many "
            f"polls means the trial may be stuck — inspect artifacts "
            f"and consider cancel_trial.\n"
            f"4. Never busy-poll (<5s): the auto-finalizer already "
            f"polls the executor every 5s; your polls add nothing below "
            f"that cadence.\n"
            f"5. Timing internals (executor timeout, finalizer cadence) "
            f"are ml-episteme internals — not yours to set. Your only "
            f"lever is poll frequency, and it should follow the "
            f"reported ETA, not a fixed guess."
        )

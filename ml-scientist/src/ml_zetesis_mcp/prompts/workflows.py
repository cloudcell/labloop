"""MCP prompt templates for the zetesis search loop — research steering."""


def register(mcp) -> None:
    """Register prompt templates on the MCP server."""

    @mcp.prompt()
    def conduct_investigation(investigation_id: str) -> str:
        """How to run a zetesis investigation — the steering contract.

        The guidance a driving client should follow between
        open_investigation and conclude_investigation.
        """
        return (
            f"You are conducting investigation {investigation_id} — a "
            "bounded inquiry into how research is done. Read "
            "search://session for the loop state.\n\n"
            "Discipline (enforced where possible, expected where not):\n\n"
            "1. State what evidence would answer the question BEFORE "
            "pulling. Write it down in your reasoning — the pulls that "
            "follow should test it, not wander.\n"
            "2. Pull contradicting evidence as deliberately as "
            "supporting evidence. An investigation that only confirms "
            "is a p-hacked literature review.\n"
            "3. A pull is not a finding and a finding is not a claim. "
            "Pulls are what you read; findings are what you concluded "
            "from them; claims exist only after conclusion, in "
            "anamnesis.\n"
            "4. record_finding gates confidence: above 0.3 requires "
            "evidence_ref_ids. If you cannot ground it, either pull "
            "more or record it at prior-level confidence — do not "
            "inflate.\n"
            "5. Prefer a null_result verdict over an unfounded finding. "
            "An honest 'no systematic pattern' is knowledge; a "
            "confident ungrounded claim is pollution of the shared "
            "memory.\n"
            "6. implications is the Loop-0 handoff: a concrete proposed "
            "programme, hypothesis, or strategy — something a client "
            "could enact via create_programme/formulate_hypothesis. "
            "Zetesis proposes; Loop 0 disposes.\n"
            "7. If the question proves ill-posed or the evidence "
            "doesn't exist, abandon_investigation — abandonment is "
            "recorded, not hidden."
        )

    @mcp.prompt()
    def scope_investigation(question: str, scope_hint: str = "") -> str:
        """Turn a vague methodological curiosity into a bounded inquiry."""
        hint = (
            f"\nScope hint provided: {scope_hint}\n" if scope_hint else ""
        )
        return (
            f"You are scoping an investigation.\n\n"
            f"Question: {question}{hint}\n\n"
            "Before calling open_investigation, make the question "
            "answerable:\n"
            "1. What evidence would answer it? (trials, conclusions, "
            "claims, lineage — name the kinds)\n"
            "2. Which Loop-0 programmes/candidates does it concern? "
            "Those go in scope.programme_ids / scope.candidate_ids.\n"
            "3. What would count as a null_result? Write it into the "
            "question or scope so concluding 'nothing found' is a "
            "pre-declared outcome, not a retreat.\n\n"
            "Then call open_investigation(question=..., scope={...})."
        )

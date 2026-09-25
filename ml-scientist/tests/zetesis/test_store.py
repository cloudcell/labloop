"""SearchStore tests — schema, lifecycle, insert-only evidence refs."""

from __future__ import annotations

from ml_zetesis_mcp.state.models import (
    EvidenceRef,
    EvidenceSource,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    InvestigationVerdict,
)


def _inv(inv_id: str, **kw) -> Investigation:
    return Investigation(
        id=inv_id, question="q?", scope={"p": 1}, **kw
    )


def _eref(ref_id: str, inv_id: str) -> EvidenceRef:
    return EvidenceRef(
        id=ref_id, investigation_id=inv_id,
        source=EvidenceSource.loop0, tool="list_trials",
        args={"a": 1}, ref_ids=["trial-x"],
    )


def test_investigation_create_and_get(search_store):
    search_store.create_investigation(_inv("inv-1"))
    inv = search_store.get_investigation("inv-1")
    assert inv is not None
    assert inv.question == "q?"
    assert inv.scope == {"p": 1}
    assert inv.status == InvestigationStatus.open
    assert inv.verdict is None


def test_get_missing_returns_none(search_store):
    assert search_store.get_investigation("inv-nope") is None
    assert search_store.get_evidence_ref("eref-nope") is None
    assert search_store.get_finding("find-nope") is None


def test_list_investigations_status_filter(search_store):
    search_store.create_investigation(_inv("inv-a"))
    search_store.create_investigation(_inv("inv-b"))
    search_store.abandon_investigation("inv-b")

    all_invs, total = search_store.list_investigations()
    assert total == 2
    assert len(all_invs) == 2

    open_invs, total = search_store.list_investigations(status="open")
    assert total == 1
    assert open_invs[0].id == "inv-a"

    ab, total = search_store.list_investigations(status="abandoned")
    assert total == 1
    assert ab[0].id == "inv-b"


def test_conclude_sets_verdict_and_timestamp(search_store):
    search_store.create_investigation(_inv("inv-1"))
    search_store.conclude_investigation(
        "inv-1", InvestigationVerdict.null_result,
        "no pattern", {"next": "try Y"},
    )
    inv = search_store.get_investigation("inv-1")
    assert inv.status == InvestigationStatus.concluded
    assert inv.verdict == InvestigationVerdict.null_result
    assert inv.summary == "no pattern"
    assert inv.implications == {"next": "try Y"}
    assert inv.concluded_at is not None


def test_evidence_refs_insert_only_and_ordered(search_store):
    search_store.create_investigation(_inv("inv-1"))
    search_store.create_evidence_ref(_eref("eref-1", "inv-1"))
    search_store.create_evidence_ref(_eref("eref-2", "inv-1"))

    refs = search_store.list_evidence_refs("inv-1")
    assert [r.id for r in refs] == ["eref-1", "eref-2"]
    assert refs[0].source == EvidenceSource.loop0
    assert refs[0].ref_ids == ["trial-x"]


def test_finding_junction_and_status(search_store):
    search_store.create_investigation(_inv("inv-1"))
    search_store.create_evidence_ref(_eref("eref-1", "inv-1"))
    search_store.create_finding(Finding(
        id="find-1", investigation_id="inv-1",
        content="X works", confidence=0.7,
    ))
    search_store.add_finding_evidence("find-1", "eref-1")

    grounding = search_store.finding_evidence_refs("find-1")
    assert [r.id for r in grounding] == ["eref-1"]

    search_store.set_finding_claim("find-1", "claim-9")
    search_store.set_finding_status("find-1", FindingStatus.asserted)
    f = search_store.get_finding("find-1")
    assert f.claim_id == "claim-9"
    assert f.status == FindingStatus.asserted


def test_list_findings_status_filter(search_store):
    search_store.create_investigation(_inv("inv-1"))
    search_store.create_finding(Finding(
        id="f1", investigation_id="inv-1", content="a", confidence=0.1,
    ))
    search_store.create_finding(Finding(
        id="f2", investigation_id="inv-1", content="b", confidence=0.2,
    ))
    search_store.set_finding_status("f2", FindingStatus.dropped)

    prov = search_store.list_findings("inv-1", status="provisional")
    assert [f.id for f in prov] == ["f1"]
    allf = search_store.list_findings("inv-1")
    assert len(allf) == 2


def test_stats(search_store):
    search_store.create_investigation(_inv("inv-1"))
    search_store.create_evidence_ref(_eref("eref-1", "inv-1"))
    search_store.create_finding(Finding(
        id="f1", investigation_id="inv-1", content="a", confidence=0.5,
    ))
    search_store.set_finding_claim("f1", "claim-1")

    stats = search_store.stats()
    assert stats["total"] == 1
    assert stats["by_status"]["open"] == 1
    assert stats["evidence_refs"] == 1
    assert stats["findings"] == 1
    assert stats["minted_claims"] == 1


def test_transaction_rolls_back_on_error(search_store):
    search_store.create_investigation(_inv("inv-1"))
    import pytest

    with pytest.raises(RuntimeError):
        with search_store.transaction():
            search_store.create_finding(Finding(
                id="f-rollback", investigation_id="inv-1",
                content="x", confidence=0.1,
            ))
            raise RuntimeError("boom")
    assert search_store.get_finding("f-rollback") is None

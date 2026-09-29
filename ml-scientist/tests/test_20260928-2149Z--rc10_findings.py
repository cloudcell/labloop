"""rc-10 diagnostic findings — plan-20260928-1709Z regression tests.

R24 incomplete_campaigns summary detail named no remedy.
R25 'decision' eref refs unreachable on arete's push path
    (list_promotion_decisions missing from the loop0 whitelist).
R26 close-before-pull wedged a campaign's verdict permanently, and
    campaigns_awaiting_verdict gated its own remediation pair.
R27 generator_seed never reached the generator under its documented
    name — byte-identical data across seeds while the DataRef row
    recorded distinct seeds and reproducibility_risk: "none".
R28 DataRef resolution existed but was undocumented and not
    ref-addressable — data_ref_paths is now injected alongside
    data_paths.
R29 correct_trial_status was non-invertible for retryable sources.
R30 'abandoned' was unreachable for a single hypothesis — no tool.
R31 decision_debt detail implied an ack could discharge it.
R32 open_campaign accepted uncarryable budgets that wedge at spawn.
R33 the injected 'next' hint replayed pre-mutation advice.
R34 create responses claimed status "created" while the stored row
    read active/proposed.
R35 cancellation/reaper receipts satisfied the completed-correction
    gate (a verdict key is not evidence of a completed run) and the
    mislabeled_outcome corrections-exemption masked the laundered row.
"""

import ast
import json

import pytest


# --- shared in-process helpers ----------------------------------------


async def _call(mcp_client, name, args):
    result = await mcp_client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if result.is_error:
            return {"error": text}
        raise


def _episteme_server(store, adaptor=None):
    from ml_episteme_mcp.clients.adaptor import (
        MCPAdaptor, create_stub_adaptor,
    )
    from ml_episteme_mcp.server import create_server

    return create_server(
        store,
        adaptor=adaptor if adaptor is not None else create_stub_adaptor(),
        enforcement_config={"recurrent_protocol": False},
    )


def _episteme_prog_hyp(store, pid="prog-1", hid="hyp-1"):
    from ml_episteme_mcp.state.models import (
        Hypothesis, Programme, ProgrammeStatus,
    )

    store.create_programme(Programme(
        id=pid, goal="g", constraints={},
        allowed_variables=["x"], budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id=hid, programme_id=pid,
        statement="s", failure_criterion="f",
        variables_involved=["x"],
    ))


# --- R24: incomplete_campaigns summary names the remedy ---------------


def _zstore(tmp_path):
    from ml_zetesis_mcp.state.store import SearchStore

    ss = SearchStore(str(tmp_path / "s.db"))
    ss.connect()
    return ss


def _campaign(cid, **kw):
    from ml_zetesis_mcp.state.models import PromotionCampaign

    return PromotionCampaign(
        id=cid, contract_id="c1",
        champion_id="cand-a", challenger_id="cand-b",
        primary_metric="hits",
        budget={"programmes_per_arm": 1, "trials_per_programme": 2},
        **kw,
    )


def _result(rid, cid, arm, created_at=None):
    from ml_zetesis_mcp.state.models import CampaignArm, CampaignResult

    kw = {"created_at": created_at} if created_at else {}
    return CampaignResult(
        id=rid, campaign_id=cid, arm=CampaignArm(arm),
        programme_id=f"prog-{rid}", metrics={"hits": 5.0}, **kw,
    )


@pytest.mark.asyncio
class TestIncompleteCampaignsDetail:
    async def test_summary_detail_names_the_remedy(self, tmp_path):
        """A summary-only reader must see the exit — sibling checks
        embed 'abandon_campaign is the exit' in detail; this one
        stopped at the bare symptom."""
        from ml_zetesis_mcp.integrity.checks import run_checks as zc
        from ml_zetesis_mcp.state.models import (
            CampaignArm, CampaignSpawn, SpawnStatus,
        )

        ss = _zstore(tmp_path)
        try:
            ss.create_campaign(_campaign(
                "camp-half", created_at="2020-01-01T00:00:00+00:00",
            ))
            ss.create_campaign_result(_result(
                "cres-1", "camp-half", "challenger",
                created_at="2020-01-01T00:00:00+00:00",
            ))
            ss.create_campaign_spawn(CampaignSpawn(
                id="spawn-1", campaign_id="camp-half",
                arm=CampaignArm.challenger, programme_id="prog-s1",
                budget={}, status=SpawnStatus.spawned,
                created_at="2020-01-01T00:00:00+00:00",
            ))
            payload = await zc(ss, stale_campaign_seconds=3600)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "incomplete_campaigns"
            )
            assert not chk["ok"]
            assert "abandon_campaign" in chk["detail"], chk["detail"]
        finally:
            ss.close()


# --- R25: list_promotion_decisions on arete's loop0 whitelist ---------


class TestAreteLoop0Whitelist:
    def test_list_promotion_decisions_whitelisted(self):
        from ml_arete_mcp.enforcement.checks import (
            check_tool_whitelisted,
        )

        assert check_tool_whitelisted(
            "loop0", "list_promotion_decisions"
        ) is None
        # The write verb stays blocked — the channel is read-only.
        assert check_tool_whitelisted(
            "loop0", "record_promotion_decision"
        ) is not None

    def test_pull_tool_enums_expose_it(self, tmp_path):
        """The tool Literal is hand-maintained per call surface —
        pin both."""
        from ml_arete_mcp.server import create_server
        from ml_arete_mcp.state.store import ImproverStore

        store = ImproverStore(str(tmp_path / "i.db"))
        store.connect()
        try:
            mcp = create_server(store)
            for tool_name in ("pull_evidence", "pull_arm_evidence"):
                tool = mcp._tool_manager.get_tool(tool_name)
                enum = (
                    tool.parameters["$defs"] if "$defs" in tool.parameters
                    else tool.parameters
                )
                text = json.dumps(tool.parameters)
                assert "list_promotion_decisions" in text, tool_name
        finally:
            store.close()

    @pytest.mark.asyncio
    async def test_decision_edge_mints_end_to_end(self, tmp_path):
        """An arete eref citing decision-* ids now mints a typed
        edge (ref_type 'decision', not 'external')."""
        from mcp.client import Client
        from ml_arete_mcp.clients.adaptors import Adaptors
        from ml_arete_mcp.server import create_server
        from ml_arete_mcp.state.models import (
            EvidenceContext, EvidenceRef, EvidenceSource,
            ImproverVersion, MetaContract, Tournament,
        )
        from ml_arete_mcp.state.store import ImproverStore

        class _FakeEvidence:
            async def pull(self, tool, args):
                assert tool == "list_promotion_decisions"
                return json.dumps({
                    "decisions": [{"id": "decision-abc123",
                                   "verdict": "promote"}]
                })

        class _RecordingClaims:
            def __init__(self):
                self.minted = []

            async def assert_claim(
                self, content, type, confidence, evidence=None,
                source_id=None,
            ):
                cid = f"claim-{len(self.minted) + 1:04d}"
                self.minted.append({"evidence": evidence or []})
                return {"claim_id": cid, "status": "created"}

        store = ImproverStore(str(tmp_path / "i.db"))
        store.connect()
        adaptors = Adaptors()
        adaptors.loop0 = _FakeEvidence()
        adaptors.claims = _RecordingClaims()
        try:
            store.create_improver(ImproverVersion(
                id="imp-p", code_artifact_digest="sha256:x",
                model_ref="m",
            ))
            store.create_improver(ImproverVersion(
                id="imp-c", parent_id="imp-p",
                code_artifact_digest="sha256:y", model_ref="m",
            ))
            store.create_meta_contract(MetaContract(
                id="mc-1", version=1,
                metrics={"primary_metric": "hits", "direction": "max"},
                promotion_policy={"min_gain": 1.0},
            ))
            store.create_tournament(Tournament(
                id="tourn-1", contract_id="mc-1",
                parent_improver_id="imp-p", candidate_improver_id="imp-c",
                budget={"descendant_runs": 1},
            ))

            client = Client(create_server(store, adaptors=adaptors))
            async with client:
                pull = await _call(client, "pull_evidence", {
                    "context_type": "tournament",
                    "context_id": "tourn-1",
                    "source": "loop0",
                    "tool": "list_promotion_decisions",
                })
                assert "error" not in pull, pull
                ref = store.get_evidence_ref(pull["evidence_ref_id"])
                assert "decision-abc123" in ref.ref_ids

                dec = await _call(client, "record_meta_decision", {
                    "candidate_improver_id": "imp-c",
                    "tournament_id": "tourn-1",
                    "verdict": "hold",
                    "decided_by": "human:tester",
                    "rationale": "cite the upstream decision",
                    "evidence_refs": [pull["evidence_ref_id"]],
                })
            assert "error" not in dec, dec
            edge_types = {
                e["to_ref"]: e["ref_type"]
                for e in adaptors.claims.minted[0]["evidence"]
            }
            assert edge_types.get("decision-abc123") == "decision", edge_types
        finally:
            store.close()


# --- R26: closed campaigns stay verdictable ---------------------------


class _ZetesisEvidence:
    """Fake evidence adaptor — get_evaluation_contract/get_incumbent
    for open_campaign, plus a generic payload for pulls."""

    def __init__(self):
        self.calls = []

    async def pull(self, tool, args):
        self.calls.append((tool, args))
        if tool == "get_evaluation_contract":
            return json.dumps({
                "contract": {"id": "contract-1",
                             "metrics": {"primary_metric": "hits"}},
            })
        if tool == "get_incumbent":
            return json.dumps({"candidate_id": "cand-a"})
        if tool == "list_programmes":
            cid = args.get("candidate_version_id")
            arm = "champion" if cid == "cand-a" else "challenger"
            return json.dumps({
                "programmes": [{"programme_id": f"prog-{arm}"}],
            })
        if tool == "list_promotion_decisions":
            return json.dumps({"decisions": [{"id": "decision-1"}]})
        return json.dumps({"ok": True, "tool": tool})


class _ZetesisPromotion:
    def __init__(self):
        self.pushed = []

    async def push(self, tool, args):
        self.pushed.append((tool, args))
        if tool == "record_promotion_decision":
            return json.dumps({"decision_id": "decision-9"})
        return json.dumps({"error": f"unexpected push: {tool}"})


def _zetesis_server(search_store):
    from ml_zetesis_mcp.clients.adaptors import Adaptors
    from ml_zetesis_mcp.server import create_server

    adaptors = Adaptors()
    adaptors.evidence = _ZetesisEvidence()
    adaptors.promotion = _ZetesisPromotion()
    return create_server(search_store, adaptors=adaptors), adaptors


async def _zcall(mcp, name, args):
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        result = await mcp.call_tool(name, args)
    except ToolError as exc:
        return {"error": str(exc)}
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


def _seed_challenger(store, cid="cand-b"):
    from ml_zetesis_mcp.state.models import RosterEntry

    store.upsert_roster_entry(RosterEntry(id=cid))


@pytest.mark.asyncio
class TestVerdictWedge:
    async def test_close_before_pull_is_recoverable(self, tmp_path):
        """The wedge sequence: close with no pulled erefs used to
        make record_promotion_verdict permanently unreachable.
        Post-close pulls + the verdict now discharge the debt."""
        ss = _zstore(tmp_path)
        try:
            mcp, _adaptors = _zetesis_server(ss)
            _seed_challenger(ss)

            opened = await _zcall(mcp, "open_campaign", {
                "contract_id": "contract-1",
                "challenger_id": "cand-b",
                "budget": {"programmes_per_arm": 1,
                           "trials_per_programme": 2},
            })
            assert "error" not in opened, opened
            cid = opened["campaign_id"]

            for arm in ("champion", "challenger"):
                r = await _zcall(mcp, "record_campaign_result", {
                    "campaign_id": cid, "arm": arm,
                    "programme_id": f"prog-{arm}",
                    "metrics": {"hits": 5.0 if arm == "champion" else 7.0},
                })
                assert "error" not in r, r

            closed = await _zcall(mcp, "close_campaign", {
                "campaign_id": cid,
            })
            assert "error" not in closed, closed

            pull = await _zcall(mcp, "pull_campaign_evidence", {
                "campaign_id": cid, "source": "loop0",
                "tool": "list_promotion_decisions",
            })
            assert "error" not in pull, pull
            eref = pull["evidence_ref_id"]

            verdict = await _zcall(mcp, "record_promotion_verdict", {
                "campaign_id": cid, "verdict": "promote",
                "decided_by": "human:tester",
                "evidence_ref_ids": [eref],
            })
            assert "error" not in verdict, verdict
            assert verdict["decision_id"] == "decision-9"

            # Post-verdict pulls mint unconsulted erefs — refused.
            late = await _zcall(mcp, "pull_campaign_evidence", {
                "campaign_id": cid, "source": "loop0",
                "tool": "list_promotion_decisions",
            })
            assert "error" in late
            assert "trail" in late["error"]
        finally:
            ss.close()

    async def test_pull_refused_on_abandoned(self, tmp_path):
        """Abandonment is a deliberate terminal exit — no further
        writes, including evidence refs."""
        from ml_zetesis_mcp.state.models import CampaignStatus

        ss = _zstore(tmp_path)
        try:
            ss.create_campaign(_campaign("camp-dead"))
            ss.abandon_campaign("camp-dead", "mis-opened", "human:x")
            assert (ss.get_campaign("camp-dead").status
                    is CampaignStatus.abandoned)
            mcp, _ = _zetesis_server(ss)
            r = await _zcall(mcp, "pull_campaign_evidence", {
                "campaign_id": "camp-dead", "source": "loop0",
                "tool": "list_trials",
            })
            assert "error" in r
            assert "abandoned" in r["error"]
        finally:
            ss.close()

    async def test_remedy_pair_exempt_from_gate(self, tmp_path):
        """The check that flags the debt must not gate the tools
        that discharge it."""
        from ml_zetesis_mcp.enforcement.recurrence import (
            check_no_open_violations,
        )
        from ml_zetesis_mcp.integrity.checks import log_dir_for
        from ml_zetesis_mcp.integrity.log import write_check_log

        ss = _zstore(tmp_path)
        try:
            write_check_log(log_dir_for(ss), {
                "server": "test",
                "checked_at": "2026-01-01T00:00:00+00:00",
                "checks": [{
                    "name": "campaigns_awaiting_verdict", "ok": False,
                    "violations": [{"campaign_id": "camp-x"}],
                    "detail": "synthetic",
                }],
            }, max_files=30)

            assert check_no_open_violations(
                ss, "pull_campaign_evidence") is None
            assert check_no_open_violations(
                ss, "record_promotion_verdict") is None
            # …but the gate still bites unrelated mutators.
            assert check_no_open_violations(ss, "open_campaign") is not None
        finally:
            ss.close()


# --- R32: open_campaign refuses uncarryable budgets -------------------


@pytest.mark.asyncio
class TestOpenCampaignBudget:
    async def test_non_carryable_budget_refused_at_open(self, tmp_path):
        """A present budget that can't spawn programmes is wedged by
        the system's own invariant — refuse it where it's created."""
        ss = _zstore(tmp_path)
        try:
            mcp, _ = _zetesis_server(ss)
            _seed_challenger(ss)
            r = await _zcall(mcp, "open_campaign", {
                "contract_id": "contract-1",
                "challenger_id": "cand-b",
                "budget": {"max_trials": 5},
            })
            assert "error" in r
            assert "budget" in r["error"]
        finally:
            ss.close()

    async def test_partial_orchestration_budget_refused(self, tmp_path):
        ss = _zstore(tmp_path)
        try:
            mcp, _ = _zetesis_server(ss)
            _seed_challenger(ss)
            r = await _zcall(mcp, "open_campaign", {
                "contract_id": "contract-1",
                "challenger_id": "cand-b",
                "budget": {"programmes_per_arm": 2},
            })
            assert "error" in r
            assert "trials_per_programme" in r["error"]
        finally:
            ss.close()

    async def test_empty_and_carryable_budgets_legal(self, tmp_path):
        """{} is the caller-driven signal; a full orchestration
        budget still opens."""
        ss = _zstore(tmp_path)
        try:
            mcp, _ = _zetesis_server(ss)
            _seed_challenger(ss, "cand-b")
            _seed_challenger(ss, "cand-c")
            empty = await _zcall(mcp, "open_campaign", {
                "contract_id": "contract-1",
                "challenger_id": "cand-b",
                "budget": {},
            })
            assert "error" not in empty, empty
            full = await _zcall(mcp, "open_campaign", {
                "contract_id": "contract-1",
                "challenger_id": "cand-c",
                "budget": {"programmes_per_arm": 1,
                           "trials_per_programme": 2},
            })
            assert "error" not in full, full
        finally:
            ss.close()


# --- R27: generator config carries seed under both spellings ----------


@pytest.mark.asyncio
class TestGeneratorSeedKeys:
    async def test_config_carries_seed_and_generator_seed(self, tmp_path):
        """A generator written against either documented name sees the
        requested seed — the silent None-fallback trap is closed."""
        from ml_episteme_mcp.clients.local_data_handler import (
            LocalDataHandler,
        )
        from ml_episteme_mcp.clients.local_executor import LocalExecutor

        gen = tmp_path / "gen.py"
        gen.write_text(
            "import json\n"
            "def generate_data(config, output_path):\n"
            "    with open(output_path, 'w') as f:\n"
            "        json.dump(config, f)\n"
        )
        handler = LocalDataHandler(
            data_dir=tmp_path / "data", executor=LocalExecutor()
        )
        ref_id = await handler.prepare_data(
            split="train", regime="generated",
            generator_code_ref=str(gen), generator_seed=17,
            generator_params={"n_samples": 3},
        )
        cfg = json.loads(
            (tmp_path / "data" / ref_id / "dataset.csv").read_text()
        )
        assert cfg["seed"] == 17
        assert cfg["generator_seed"] == 17
        assert cfg["n_samples"] == 3

    async def test_params_cannot_override_recorded_seed(self, tmp_path):
        """A param-side 'seed' key is superseded — the DataRef row
        records generator_seed, so delivered seed must equal it."""
        from ml_episteme_mcp.clients.local_data_handler import (
            LocalDataHandler,
        )
        from ml_episteme_mcp.clients.local_executor import LocalExecutor

        gen = tmp_path / "gen.py"
        gen.write_text(
            "import json\n"
            "def generate_data(config, output_path):\n"
            "    with open(output_path, 'w') as f:\n"
            "        json.dump(config, f)\n"
        )
        handler = LocalDataHandler(
            data_dir=tmp_path / "data", executor=LocalExecutor()
        )
        ref_id = await handler.prepare_data(
            split="train", regime="generated",
            generator_code_ref=str(gen), generator_seed=17,
            generator_params={"seed": 999, "generator_seed": 888},
        )
        cfg = json.loads(
            (tmp_path / "data" / ref_id / "dataset.csv").read_text()
        )
        assert cfg["seed"] == 17
        assert cfg["generator_seed"] == 17


# --- R28: run_trial injects data_ref_paths ----------------------------


class _CapturingExecutor:
    """Executor that records the wrapper code — the config is embedded
    in it as a literal."""

    def __init__(self):
        self.codes: list[str] = []

    async def execute_code(self, code, **kwargs):
        self.codes.append(code)
        return json.dumps({"status": "completed"})

    async def execute_code_async(self, trial_id, code, **kwargs):
        self.codes.append(code)
        return json.dumps({"status": "completed"})

    def get_async_status(self, trial_id):
        return json.dumps({"status": "running"})

    async def cancel_async(self, trial_id):
        return json.dumps({"status": "cancelled"})


@pytest.mark.asyncio
class TestDataRefPathsInjection:
    async def test_data_ref_paths_in_config(self, tmp_path):
        """The bundle's data_refs resolve to read-only paths the trial
        can address by ref id — not just by split."""
        from mcp.client import Client
        from ml_episteme_mcp.clients.adaptor import create_stub_adaptor
        from ml_episteme_mcp.state.models import (
            Bundle, DataRef, Programme, ProgrammeStatus, Trial,
            TrialStatus,
        )
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        data_dir = tmp_path / "data" / "data-ref-1"
        data_dir.mkdir(parents=True)
        (data_dir / "dataset.csv").write_text("x\n1\n")
        try:
            _episteme_prog_hyp(store)
            store.create_data_ref(DataRef(
                id="data-ref-1", split="train", regime="captured",
                source_uri="file:///x", content_hash="h",
                storage_uri=str(data_dir),
            ))
            store.create_trial(Trial(
                id="trial-1", programme_id="prog-1",
                hypothesis_id="hyp-1", config_json='{"lr": 0.1}',
                status=TrialStatus.designed,
            ))
            store.create_bundle(Bundle(
                id="bundle-1", trial_id="trial-1",
                code_ref="tests/fixtures/train_stub.py",
                env_ref="conda:x", seeds_json="[1]",
                splits_json="{}", data_refs_json='["data-ref-1"]',
            ))
            store.link_bundle("trial-1", "bundle-1")

            ex = _CapturingExecutor()
            adaptor = create_stub_adaptor()
            adaptor.set_executor(ex)
            # Point the stub data source at the seeded ref.
            adaptor.data_source._refs["data-ref-1"] = {
                "id": "data-ref-1", "split": "train",
                "storage_uri": str(data_dir),
            }
            mcp = _episteme_server(store, adaptor)
            client = Client(mcp)
            async with client:
                r = await _call(client, "run_trial", {
                    "programme_id": "prog-1", "trial_id": "trial-1",
                })
            assert "error" not in r, r

            # Extract the embedded config literal from the wrapper.
            code = ex.codes[0]
            cfg = None
            for line in code.splitlines():
                if line.startswith("config = "):
                    cfg = ast.literal_eval(line[len("config = "):])
            assert cfg is not None, "no config literal in wrapper"
            assert cfg["lr"] == 0.1
            assert cfg["data_ref_paths"] == {"data-ref-1": str(data_dir)}
            assert cfg["data_paths"] == {"train": str(data_dir)}
        finally:
            store.close()


# --- R29: correct_trial_status accepts retryable sources --------------


class TestCorrectTrialStatus:
    def test_retryable_source_round_trip(self, tmp_path):
        """completed→retryable→failed: a mistaken correction is a
        recorded repair, not a permanent one."""
        from ml_episteme_mcp.state.models import Trial, TrialStatus
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            store.create_trial(Trial(
                id="trial-1", programme_id="prog-1",
                hypothesis_id="hyp-1", config_json="{}",
                status=TrialStatus.completed,
                executor_output_json='{"status": "completed"}',
            ))
            store.correct_trial_status("trial-1", "retryable", "mistake")
            assert store.get_trial("trial-1").status is TrialStatus.retryable
            store.correct_trial_status("trial-1", "failed", "re-read")
            assert store.get_trial("trial-1").status is TrialStatus.failed
            trail = json.loads(
                store.get_trial("trial-1").executor_output_json
            )["corrections"]
            assert len(trail) == 2
        finally:
            store.close()

    def test_retryable_to_completed_needs_executor_record(self, tmp_path):
        """The widened source set doesn't weaken the evidentiary
        gate — 'completed' still requires a recorded execution."""
        from ml_episteme_mcp.state.models import Trial, TrialStatus
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            store.create_trial(Trial(
                id="trial-1", programme_id="prog-1",
                hypothesis_id="hyp-1", config_json="{}",
                status=TrialStatus.retryable,
            ))
            with pytest.raises(ValueError):
                store.correct_trial_status("trial-1", "completed", "no record")
        finally:
            store.close()


# --- R35: a verdict-key record is not evidence of completion ---------


def _trial(store, tid="trial-1", status="failed", record=None):
    """Create a trial row; prog-1/hyp-1 must already exist."""
    from ml_episteme_mcp.state.models import Trial, TrialStatus

    store.create_trial(Trial(
        id=tid, programme_id="prog-1",
        hypothesis_id="hyp-1", config_json="{}",
        status=TrialStatus(status),
        executor_output_json=record,
    ))


class TestCompletionEvidenceGate:
    """R35: 'completed' requires a record evidencing a completed run.
    Cancellation receipts, reaper notes, and failure outputs all carry
    verdict keys — presence is not proof."""

    @pytest.mark.parametrize("record", [
        '{"status": "cancelled", "trial_id": "trial-1"}',
        '{"status": "cancelled", "trial_id": "trial-1", "tombstoned": true}',
        '{"status": "failed", "error": "Trial trial-1 already finished"}',
        '{"status": "failed", "interrupted": true, '
        '"error": "server restarted while trial was running"}',
        '{"status": "timeout", "exit_code": -1, "error": "exceeded"}',
        '{"status": "completed", "exit_code": 0, '
        '"stdout": "{\\"status\\": \\"error\\", \\"error\\": \\"boom\\"}"}',
    ])
    def test_noncompletion_records_refuse_completed(self, tmp_path, record):
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            for source in ("failed", "retryable"):
                _trial(store, f"trial-{source}", status=source,
                       record=record)
                with pytest.raises(ValueError, match="evidence"):
                    store.correct_trial_status(
                        f"trial-{source}", "completed", "launder"
                    )
        finally:
            store.close()

    def test_running_transition_also_gated(self, tmp_path):
        """update_trial_status holds the same invariant — a running
        trial whose record documents non-completion cannot finalize
        'completed'."""
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="designed")
            store.update_trial_status("trial-1", "running")
            store.update_trial_executor_output(
                "trial-1", '{"status": "cancelled"}'
            )
            with pytest.raises(ValueError, match="evidence"):
                store.update_trial_status("trial-1", "completed")
        finally:
            store.close()

    def test_clean_record_still_correctable(self, tmp_path):
        """A genuine completed record remains correctable — evidence
        re-read after the fact is the legitimate use of the tool."""
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="failed",
                   record='{"status": "completed", "exit_code": 0}')
            store.correct_trial_status(
                "trial-1", "completed", "re-read: executor succeeded"
            )
            assert store.get_trial("trial-1").status.value == "completed"
        finally:
            store.close()

    @pytest.mark.asyncio
    async def test_tool_path_mark_retryable_then_correct(self, tmp_path):
        """End to end: mark_retryable persists the cancel receipt; the
        subsequent completed-correction is refused at the tool."""
        import json as _json

        from mcp.client import Client
        from ml_episteme_mcp.clients.adaptor import create_stub_adaptor
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="designed")
            store.update_trial_status("trial-1", "running")
            adaptor = create_stub_adaptor()

            async def _cancel(tid):
                return _json.dumps(
                    {"status": "cancelled", "trial_id": tid}
                )

            adaptor.executor.cancel_async = _cancel
            client = Client(_episteme_server(store, adaptor))
            async with client:
                r = await _call(client, "mark_retryable", {
                    "programme_id": "prog-1", "trial_id": "trial-1",
                    "reason": "stuck",
                })
                assert "error" not in r, r
                # The receipt landed on the row — this is what used to
                # unlock the completed-correction.
                out = _json.loads(
                    store.get_trial("trial-1").executor_output_json
                )
                assert out["status"] == "cancelled"
                r = await _call(client, "correct_trial_status", {
                    "programme_id": "prog-1", "trial_id": "trial-1",
                    "to_status": "completed",
                    "reason": "it actually ran",
                })
                assert "error" in r
                assert store.get_trial(
                    "trial-1"
                ).status.value == "retryable"
        finally:
            store.close()

    def test_laundered_row_is_flagged(self, tmp_path):
        """A completed row whose record is a cancellation receipt plus
        a corrections trail is flagged by mislabeled_outcome — the
        exemption that masked this shape is gone."""
        from ml_episteme_mcp.integrity.checks import run_checks
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="designed")
            store.update_trial_status("trial-1", "running")
            store.update_trial_executor_output(
                "trial-1", _json_receipt()
            )
            # Bypass the gate the way a pre-fix launder did.
            store.conn.execute(
                "UPDATE trials SET status='completed' WHERE id='trial-1'"
            )
            store.conn.commit()
            checks = {
                c["name"]: c for c in run_checks(store)["checks"]
            }
            c = checks["mislabeled_outcome"]
            assert not c["ok"]
            assert c["violations"][0]["trial_id"] == "trial-1"
        finally:
            store.close()


def _json_receipt():
    import json as _json

    return _json.dumps({
        "status": "cancelled", "trial_id": "trial-1",
        "corrections": [{"from": "retryable", "to": "completed",
                         "reason": "x", "corrected_at": "t"}],
    })


# --- R30: abandon_hypothesis ------------------------------------------


@pytest.mark.asyncio
class TestAbandonHypothesis:
    _ARGS = {
        "programme_id": "prog-1", "hypothesis_id": "hyp-1",
        "rationale": "only failing trials exist — line is dead",
        "decided_by": "human:tester",
    }

    async def test_under_test_abandons(self, tmp_path):
        """Attribution lands on the row: who decided, why, when."""
        from mcp.client import Client
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            store.update_hypothesis_status("hyp-1", "under_test")
            client = Client(_episteme_server(store))
            async with client:
                r = await _call(client, "abandon_hypothesis", self._ARGS)
            assert "error" not in r, r
            assert r["status"] == "abandoned"
            assert r["from_status"] == "under_test"
            h = store.get_hypothesis("hyp-1")
            assert h.status.value == "abandoned"
            assert h.abandoned_by == "human:tester"
            assert h.abandon_rationale == self._ARGS["rationale"]
            assert h.abandoned_at is not None
        finally:
            store.close()

    async def test_attribution_required(self, tmp_path):
        """A governance act without a decider or a why is refused."""
        from mcp.client import Client
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            client = Client(_episteme_server(store))
            async with client:
                r = await _call(client, "abandon_hypothesis", {
                    **self._ARGS, "rationale": "",
                })
                assert "rationale" in r["error"]
                r = await _call(client, "abandon_hypothesis", {
                    **self._ARGS, "decided_by": "  ",
                })
                assert "decided_by" in r["error"]
            assert store.get_hypothesis("hyp-1").status.value == "proposed"
        finally:
            store.close()

    async def test_terminal_states_refuse(self, tmp_path):
        from mcp.client import Client
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            store.update_hypothesis_status("hyp-1", "under_test")
            store.abandon_hypothesis("hyp-1", "done", "human:x")
            client = Client(_episteme_server(store))
            async with client:
                r = await _call(client, "abandon_hypothesis", self._ARGS)
            assert "error" in r
            assert "transition" in r["error"].lower()
        finally:
            store.close()

    async def test_orphan_hypothesis_refused(self, tmp_path):
        from mcp.client import Client
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            client = Client(_episteme_server(store))
            async with client:
                r = await _call(client, "abandon_hypothesis", {
                    **self._ARGS, "programme_id": "prog-other",
                })
            assert "error" in r
            assert "programme" in r["error"].lower()
        finally:
            store.close()

    async def test_conclude_refusal_names_the_exit(self, tmp_path):
        """A failed-only hypothesis can't conclude — the refusal must
        say where it CAN go."""
        from ml_episteme_mcp.enforcement.commitments import (
            check_evidence_exists,
        )
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            err = check_evidence_exists("prog-1", "hyp-1", store)
            assert err is not None
            assert "abandon_hypothesis" in err
        finally:
            store.close()


# --- R31: decision_debt detail is honest about acks -------------------


@pytest.mark.asyncio
class TestDecisionDebtDetail:
    async def test_detail_states_ack_does_not_discharge(self, tmp_path):
        from ml_arete_mcp.integrity.checks import run_checks
        from ml_arete_mcp.state.models import (
            ImproverVersion, MetaContract, Tournament,
        )
        from ml_arete_mcp.state.store import ImproverStore

        store = ImproverStore(str(tmp_path / "i.db"))
        store.connect()
        try:
            store.create_improver(ImproverVersion(
                id="imp-p", code_artifact_digest="sha256:x",
                model_ref="m",
            ))
            store.create_improver(ImproverVersion(
                id="imp-c", parent_id="imp-p",
                code_artifact_digest="sha256:y", model_ref="m",
            ))
            store.create_meta_contract(MetaContract(
                id="mc-1", version=1,
                metrics={"primary_metric": "hits", "direction": "max"},
                promotion_policy={"min_gain": 1.0},
            ))
            store.create_tournament(Tournament(
                id="tourn-1", contract_id="mc-1",
                parent_improver_id="imp-p", candidate_improver_id="imp-c",
                budget={"descendant_runs": 1},
            ))
            store.close_tournament("tourn-1", recursive_gain=0.5)
            payload = await run_checks(store)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "decision_debt"
            )
            assert not chk["ok"]
            assert "acknowledge_violation" in chk["detail"]
            assert "does NOT discharge" in chk["detail"]
        finally:
            store.close()


# --- R33: post-mutation 'next' hints are suppressed -------------------


class TestNextHintStaleness:
    @pytest.mark.parametrize("module", [
        "ml_episteme_mcp.enforcement.recurrence",
        "ml_zetesis_mcp.enforcement.recurrence",
        "ml_arete_mcp.enforcement.recurrence",
        "ml_anamnesis_mcp.enforcement.recurrence",
    ])
    def test_hint_suppressed_after_mutation(self, module):
        """The digest predates the mutation — advice computed from it
        is unverified until the client re-reads status."""
        import importlib
        import time

        rec = importlib.import_module(module)
        tracker = rec.RecurrenceTracker()
        tracker.configure(60)
        digest = {
            "recommended_next": [{
                "action": "close", "tool": "close_x", "reason": "r",
            }],
            "blockers": [],
        }
        tracker.mark_status_read(digest)
        assert tracker.next_hint() is not None

        tracker.note_mutation()
        assert tracker.next_hint() is None

        # A fresh status read restores hints.
        tracker.mark_status_read(digest)
        assert tracker.next_hint() is not None
        tracker.reset()
        assert tracker.next_hint() is None


# --- R34: create responses echo the stored status ---------------------


@pytest.mark.asyncio
class TestCreateStatusEcho:
    async def test_create_programme_echoes_active(self, tmp_path):
        from mcp.client import Client
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            client = Client(_episteme_server(store))
            async with client:
                r = await _call(client, "create_programme", {
                    "goal": "g", "constraints": {},
                    "allowed_variables": ["x"],
                    "budget": {"max_trials": 5,
                               "max_wall_time_hours": 1.0},
                })
            assert "error" not in r, r
            assert r["status"] == "active"
            assert store.get_programme(r["programme_id"]).status.value == "active"
        finally:
            store.close()

    async def test_formulate_hypothesis_echoes_proposed(self, tmp_path):
        from mcp.client import Client
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store, hid="hyp-seed")
            client = Client(_episteme_server(store))
            async with client:
                r = await _call(client, "formulate_hypothesis", {
                    "programme_id": "prog-1",
                    "statement": "x matters",
                    "failure_criterion": "x does not matter",
                    "variables_involved": ["x"],
                })
            assert "error" not in r, r
            assert r["status"] == "proposed"
            assert (store.get_hypothesis(r["hypothesis_id"])
                    .status.value == "proposed")
        finally:
            store.close()

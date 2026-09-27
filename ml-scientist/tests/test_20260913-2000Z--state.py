"""Phase 2 tests: state layer — create/store/retrieve, MECE enforcement,
aboutness, and bundle durability.

These tests validate the done-when criteria from the roadmap Phase 2:
  - a hypothesis can be created, stored, retrieved
  - aboutness and bundle are durable
  - MECE single-category placement is enforced
"""

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.state.models import (
    Belief,
    Bundle,
    Conclusion,
    ENTITY_CATEGORIES,
    Hypothesis,
    Observation,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
    Verdict,
    verify_mece,
)
from ml_episteme_mcp.state.store import StateStore


# --- Fixtures ---


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = StateStore(path)
    s.connect()
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def programme():
    return Programme(
        id="prog-1",
        goal="maximize validation accuracy",
        constraints={"gpu_memory_gb": 40, "max_training_hours_per_trial": 4},
        allowed_variables=["learning_rate", "depth", "optimizer"],
        budget_max_trials=50,
        budget_max_wall_time_hours=200.0,
    )


@pytest.fixture
def hypothesis(programme):
    return Hypothesis(
        id="hyp-1",
        programme_id=programme.id,
        statement="depth improves generalization under a 40GB / 4-hour budget",
        failure_criterion="val_accuracy(depth=12) <= val_accuracy(depth=6) with p<0.05",
        variables_involved=["depth"],
    )


@pytest.fixture
def bundle():
    return Bundle(
        id="bundle-1",
        trial_id="trial-1",
        code_ref="git:abc123",
        env_ref="conda:env-v1",
        seeds_json=json.dumps([42, 43, 44]),
        splits_json=json.dumps({"train": "0.8", "val": "0.1", "test": "0.1"}),
        baseline_ref="config:baseline-v1",
    )


@pytest.fixture
def trial(programme, hypothesis):
    return Trial(
        id="trial-1",
        programme_id=programme.id,
        hypothesis_id=hypothesis.id,
        config_json=json.dumps({"learning_rate": 0.001, "depth": 12}),
    )


@pytest.fixture
def observation(trial):
    return Observation(
        id="obs-1",
        trial_id=trial.id,
        metrics_json=json.dumps({"val_accuracy": 0.92, "train_loss": 0.15}),
        variance_json=json.dumps({"val_accuracy": 0.003, "train_loss": 0.01}),
        spatiotemporal_region="host:gpu-0@2026-09-14T03:00Z",
    )


@pytest.fixture
def belief(programme):
    return Belief(
        id="belief-1",
        programme_id=programme.id,
        state_json=json.dumps({"posterior": {"depth": {"positive": 0.7, "negative": 0.3}}}),
    )


@pytest.fixture
def conclusion(hypothesis, programme):
    return Conclusion(
        id="conc-1",
        hypothesis_id=hypothesis.id,
        programme_id=programme.id,
        verdict=Verdict.accepted,
        evidence_ref="obs-1,belief-1",
        evidence_summary="val_accuracy(depth=12) > val_accuracy(depth=6) with p<0.01",
    )


# --- MECE enforcement (Rule 5.1) ---


class TestMECE:
    def test_each_entity_has_single_category(self):
        """Rule 5.1: every entity has exactly one terminal category."""
        assert len(ENTITY_CATEGORIES) == 12

    def test_verify_mece_passes_for_correct_categories(
        self, programme, hypothesis, trial, observation, belief, conclusion, bundle
    ):
        for entity in [programme, hypothesis, trial, observation, belief, conclusion, bundle]:
            verify_mece(entity)  # should not raise

    def test_verify_mece_rejects_wrong_category(self, programme):
        """Rule 5.1: a Programme must be information content entity, not data item."""
        programme.ontological_category = "data item"
        with pytest.raises(ValueError, match="MECE violation"):
            verify_mece(programme)

    def test_verify_mece_rejects_unknown_type(self):
        from pydantic import BaseModel

        class Unknown(BaseModel):
            ontological_category: str = "process"
            aboutness: str = "test"

        with pytest.raises(ValueError, match="Unknown entity type"):
            verify_mece(Unknown())


# --- Aboutness tracking (Rule 5.3) ---


class TestAboutness:
    def test_every_entity_has_aboutness(
        self, programme, hypothesis, trial, observation, belief, conclusion, bundle
    ):
        """Rule 5.3: every ICE records what it is about."""
        for entity in [programme, hypothesis, trial, observation, belief, conclusion, bundle]:
            assert hasattr(entity, "aboutness")
            assert entity.aboutness != ""

    def test_aboutness_is_distinct_per_type(
        self, programme, hypothesis, trial, observation, belief, conclusion, bundle
    ):
        """Each entity type has a distinct aboutness description."""
        aboutnesses = [
            programme.aboutness,
            hypothesis.aboutness,
            trial.aboutness,
            observation.aboutness,
            belief.aboutness,
            conclusion.aboutness,
            bundle.aboutness,
        ]
        assert len(set(aboutnesses)) == 7


# --- Create / Store / Retrieve ---


class TestProgrammeCRUD:
    def test_create_and_retrieve(self, store, programme):
        store.create_programme(programme)
        retrieved = store.get_programme(programme.id)
        assert retrieved is not None
        assert retrieved.id == programme.id
        assert retrieved.goal == programme.goal
        assert retrieved.constraints == programme.constraints
        assert retrieved.allowed_variables == programme.allowed_variables
        assert retrieved.budget_max_trials == programme.budget_max_trials
        assert retrieved.budget_max_wall_time_hours == programme.budget_max_wall_time_hours
        assert retrieved.status == ProgrammeStatus.active

    def test_retrieve_nonexistent(self, store):
        assert store.get_programme("does-not-exist") is None


class TestHypothesisCRUD:
    def test_create_and_retrieve(self, store, programme, hypothesis):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        retrieved = store.get_hypothesis(hypothesis.id)
        assert retrieved is not None
        assert retrieved.id == hypothesis.id
        assert retrieved.statement == hypothesis.statement
        assert retrieved.failure_criterion == hypothesis.failure_criterion
        assert retrieved.variables_involved == hypothesis.variables_involved

    def test_list_by_programme(self, store, programme, hypothesis):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        hyps = store.list_hypotheses(programme.id)
        assert len(hyps) == 1
        assert hyps[0].id == hypothesis.id


class TestTrialCRUD:
    def test_create_and_retrieve(self, store, programme, hypothesis, trial):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_trial(trial)
        retrieved = store.get_trial(trial.id)
        assert retrieved is not None
        assert retrieved.id == trial.id
        assert retrieved.programme_id == programme.id
        assert retrieved.hypothesis_id == hypothesis.id

    def test_update_status(self, store, programme, hypothesis, trial):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_trial(trial)
        store.update_trial_status(trial.id, "running")
        store.update_trial_executor_output(trial.id, '{"status": "completed", "exit_code": 0}')
        store.update_trial_status(trial.id, "completed")
        retrieved = store.get_trial(trial.id)
        assert retrieved.status.value == "completed"

    def test_link_bundle(self, store, programme, hypothesis, trial, bundle):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_trial(trial)
        store.create_bundle(bundle)
        store.link_bundle(trial.id, bundle.id)
        retrieved = store.get_trial(trial.id)
        assert retrieved.bundle_id == bundle.id


class TestBundleCRUD:
    def test_create_and_retrieve(self, store, programme, hypothesis, trial, bundle):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_trial(trial)
        store.create_bundle(bundle)
        retrieved = store.get_bundle(bundle.id)
        assert retrieved is not None
        assert retrieved.code_ref == bundle.code_ref
        assert retrieved.env_ref == bundle.env_ref
        assert retrieved.seeds_json == bundle.seeds_json
        assert retrieved.splits_json == bundle.splits_json
        assert retrieved.baseline_ref == bundle.baseline_ref

    def test_bundle_completeness(self, bundle):
        """Commitment 6: the bundle must be controlled (all fields captured)."""
        assert bundle.is_complete() is True

    def test_bundle_incomplete_without_baseline(self):
        """Baseline is optional; bundle is still complete without it."""
        b = Bundle(
            id="b2",
            trial_id="t2",
            code_ref="git:def",
            env_ref="conda:env2",
            seeds_json="[1,2]",
            splits_json="{}",
            baseline_ref=None,
        )
        assert b.is_complete() is True


class TestObservationCRUD:
    def test_create_and_retrieve(self, store, programme, hypothesis, trial, observation):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_trial(trial)
        store.create_observation(observation)
        retrieved = store.get_observation(observation.id)
        assert retrieved is not None
        assert retrieved.trial_id == trial.id
        assert json.loads(retrieved.metrics_json)["val_accuracy"] == 0.92
        assert json.loads(retrieved.variance_json)["val_accuracy"] == 0.003

    def test_list_by_trial(self, store, programme, hypothesis, trial, observation):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_trial(trial)
        store.create_observation(observation)
        obs = store.list_observations(trial.id)
        assert len(obs) == 1
        assert obs[0].id == observation.id


class TestBeliefCRUD:
    def test_create_and_retrieve_latest(self, store, programme, belief):
        store.create_programme(programme)
        store.create_belief(belief)
        retrieved = store.get_belief(programme.id)
        assert retrieved is not None
        assert retrieved.id == belief.id
        assert json.loads(retrieved.state_json)["posterior"]["depth"]["positive"] == 0.7

    def test_latest_belief_is_most_recent(self, store, programme):
        store.create_programme(programme)
        b1 = Belief(
            id="b1",
            programme_id=programme.id,
            state_json=json.dumps({"v": 1}),
            updated_at="2026-09-14T01:00:00+00:00",
        )
        b2 = Belief(
            id="b2",
            programme_id=programme.id,
            state_json=json.dumps({"v": 2}),
            updated_at="2026-09-14T02:00:00+00:00",
        )
        store.create_belief(b1)
        store.create_belief(b2)
        retrieved = store.get_belief(programme.id)
        assert retrieved.id == "b2"


class TestConclusionCRUD:
    def test_create_and_retrieve(self, store, programme, hypothesis, conclusion):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_conclusion(conclusion)
        retrieved = store.get_conclusion(conclusion.id)
        assert retrieved is not None
        assert retrieved.verdict == Verdict.accepted
        assert retrieved.evidence_summary == conclusion.evidence_summary

    def test_list_by_programme(self, store, programme, hypothesis, conclusion):
        store.create_programme(programme)
        store.create_hypothesis(hypothesis)
        store.create_conclusion(conclusion)
        concs = store.list_conclusions(programme.id)
        assert len(concs) == 1
        assert concs[0].id == conclusion.id


# --- Carrier / Content separation (Rule 5.4, b-01 §2.4) ---


class TestCarrierContentSeparation:
    def test_backup_preserves_content(self, programme, hypothesis):
        """The file is the carrier; the rows are the content. Backing up
        the file preserves the content (generically dependent continuant)."""
        import shutil

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        backup_path = path + ".bak"
        try:
            s1 = StateStore(path)
            s1.connect()
            s1.create_programme(programme)
            s1.create_hypothesis(hypothesis)
            s1.close()

            # Backup the carrier (the file) — copy, not move
            shutil.copy2(path, backup_path)

            # Restore into a new carrier
            s2 = StateStore(backup_path)
            s2.connect()
            retrieved = s2.get_hypothesis(hypothesis.id)
            assert retrieved is not None
            assert retrieved.statement == hypothesis.statement
            s2.close()
        finally:
            Path(path).unlink(missing_ok=True)
            Path(backup_path).unlink(missing_ok=True)


# --- Full lifecycle: programme → hypothesis → trial → observation → belief → conclusion ---


class TestFullLifecycle:
    def test_end_to_end_state_lifecycle(
        self, store, programme, hypothesis, bundle, trial, observation, belief, conclusion
    ):
        """The full lifecycle of a single experiment through the state layer."""
        # 1. Create programme
        store.create_programme(programme)
        assert store.get_programme(programme.id) is not None

        # 2. Formulate hypothesis
        store.create_hypothesis(hypothesis)
        assert store.get_hypothesis(hypothesis.id) is not None

        # 3. Design experiment (create trial)
        store.create_trial(trial)
        assert store.get_trial(trial.id) is not None

        # 4. Capture bundle (commitment 6)
        store.create_bundle(bundle)
        store.link_bundle(trial.id, bundle.id)
        linked_trial = store.get_trial(trial.id)
        assert linked_trial.bundle_id == bundle.id
        linked_bundle = store.get_bundle(linked_trial.bundle_id)
        assert linked_bundle.is_complete()

        # 5. Execute (update status)
        store.update_trial_status(trial.id, "running")
        store.update_trial_executor_output(trial.id, '{"status": "completed", "exit_code": 0}')
        store.update_trial_status(trial.id, "completed")
        assert store.get_trial(trial.id).status.value == "completed"

        # 6. Record observation
        store.create_observation(observation)
        assert store.get_observation(observation.id) is not None

        # 7. Update belief
        store.create_belief(belief)
        assert store.get_belief(programme.id) is not None

        # 8. Close programme (create conclusion)
        store.create_conclusion(conclusion)
        retrieved_conc = store.get_conclusion(conclusion.id)
        assert retrieved_conc.verdict == Verdict.accepted

        # Verify the full chain is queryable
        all_hyps = store.list_hypotheses(programme.id)
        all_trials = store.list_trials(programme.id)
        all_obs = store.list_observations(trial.id)
        all_concs = store.list_conclusions(programme.id)
        assert len(all_hyps) == 1
        assert len(all_trials) == 1
        assert len(all_obs) == 1
        assert len(all_concs) == 1


class TestProgrammeSorting:
    """list_programmes_paginated sort by latest activity timestamp."""

    def _mk_programme(self, store, pid, created_at):
        store.create_programme(Programme(
            id=pid,
            goal=f"goal {pid}",
            constraints={"type": "test"},
            allowed_variables=["x"],
            budget_max_trials=5,
            budget_max_wall_time_hours=1.0,
            status=ProgrammeStatus.active,
        ))
        store._write(
            "UPDATE programmes SET created_at = ? WHERE id = ?",
            (created_at, pid),
        )

    def test_activity_desc_orders_by_latest_event(self, store):
        """A programme with a newer trial outranks a newer-created one."""
        self._mk_programme(store, "prog-old", "2026-09-14T00:00:00+00:00")
        self._mk_programme(store, "prog-new", "2026-09-14T10:00:00+00:00")

        # Default created_desc: newest first
        rows = store.list_programmes_paginated(sort="created_desc")
        assert [r["id"] for r in rows] == ["prog-new", "prog-old"]

        # Give prog-old a later trial → it wins on activity
        store.create_hypothesis(Hypothesis(
            id="hyp-old", programme_id="prog-old",
            statement="s", failure_criterion="f", variables_involved=["x"],
        ))
        store.create_trial(Trial(
            id="trial-old", programme_id="prog-old", hypothesis_id="hyp-old",
            config_json="{}", status=TrialStatus.designed,
        ))
        # Backdate both (create_* stamps them with now())
        store._write(
            "UPDATE hypotheses SET created_at = ? WHERE id = 'hyp-old'",
            ("2026-09-14T01:00:00+00:00",),
        )
        store._write(
            "UPDATE trials SET created_at = ? WHERE id = 'trial-old'",
            ("2026-09-14T12:00:00+00:00",),
        )

        rows = store.list_programmes_paginated(sort="activity_desc")
        assert [r["id"] for r in rows] == ["prog-old", "prog-new"]
        assert rows[0]["last_activity"] == "2026-09-14T12:00:00+00:00"

    def test_activity_asc_reverse(self, store):
        self._mk_programme(store, "prog-a", "2026-09-14T00:00:00+00:00")
        self._mk_programme(store, "prog-b", "2026-09-14T10:00:00+00:00")
        rows = store.list_programmes_paginated(sort="activity_asc")
        assert [r["id"] for r in rows] == ["prog-a", "prog-b"]

    def test_last_activity_counts_beliefs_and_archiving(self, store):
        """Belief updates and archive entries count as activity."""
        self._mk_programme(store, "prog-p", "2026-09-14T00:00:00+00:00")

        store.create_belief(Belief(
            id="belief-p", programme_id="prog-p", state_json="{}",
        ))
        store._write(
            "UPDATE beliefs SET updated_at = ? WHERE programme_id = 'prog-p'",
            ("2026-09-14T08:00:00+00:00",),
        )
        rows = store.list_programmes_paginated(sort="activity_desc")
        assert rows[0]["last_activity"] == "2026-09-14T08:00:00+00:00"

        # A later archive_entries.archived_at outranks the belief update
        store._write(
            "INSERT INTO archive_registry (archive_id, archive_path, "
            "created_at, batch_size, sealed) "
            "VALUES ('arch-x', '/tmp/x.db', '2026-09-14T09:00:00+00:00', 10, 0)"
        )
        store._write(
            "INSERT INTO archive_entries (id, archive_id, programme_id, "
            "programme_goal, archived_at, row_count, verified) "
            "VALUES ('ae-x', 'arch-x', 'prog-p', 'g', "
            "'2026-09-14T09:30:00+00:00', 1, 1)"
        )
        rows = store.list_programmes_paginated(sort="activity_desc")
        assert rows[0]["last_activity"] == "2026-09-14T09:30:00+00:00"

    def test_last_activity_never_null(self, store):
        """A programme with no events falls back to created_at."""
        self._mk_programme(store, "prog-bare", "2026-09-14T05:00:00+00:00")
        rows = store.list_programmes_paginated(sort="activity_desc")
        assert rows[0]["last_activity"] == "2026-09-14T05:00:00+00:00"

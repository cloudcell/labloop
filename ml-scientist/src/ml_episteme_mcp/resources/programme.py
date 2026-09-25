"""MCP resource handlers — programme:// and trial:// URI templates."""

from __future__ import annotations

import json
from pathlib import Path

from ..state.store import StateStore


def register(mcp, store: StateStore) -> None:
    """Register resource handlers on the MCP server."""

    @mcp.resource("executor://contract")
    def get_executor_contract() -> str:
        """The executor contract — how run_training must be implemented.

        This resource documents the interface that the bundle's code_ref
        must satisfy for run_trial to execute successfully.
        """
        return json.dumps(
            {
                "entrypoint": "run_training",
                "signature": "def run_training(config: dict) -> dict",
                "config": "The same dict passed to design_experiment",
                "returns": {
                    "metrics": "dict[str, float] — measured values (e.g. val_accuracy, val_perplexity)",
                    "variance": "dict[str, float] — variance across seeds (required by commitment 7)",
                },
                "failure": (
                    "Failure must be visible as a NONZERO EXIT or an "
                    "explicit error payload — raise on a crashed inner "
                    "job, or return {'status': 'error', ...} / a dict "
                    "with an 'error' key / nonzero '_exit_code'. The "
                    "harness marks the trial failed on any of these. "
                    "Returning a success-shaped dict while swallowing a "
                    "crash mislabels the trial 'completed' — the "
                    "mislabeled_outcome integrity check flags it and "
                    "correct_trial_status repairs it (recorded). "
                    "An executor that emits no recordable output at "
                    "all finalizes as 'failed': 'completed' requires "
                    "a persisted executor record — the store refuses "
                    "to write an unevidenced completion."
                ),
                "code_ref_requirement": (
                    "bundle.code_ref must be a path to a .py file that exposes "
                    "run_training(config) -> dict. The file is imported via "
                    "importlib and run_training is called with the trial config. "
                    "The return value must be printed to stdout as JSON (the "
                    "server wrapper handles this)."
                ),
                "concurrency": (
                    "Multiple trials may execute CONCURRENTLY. run_training "
                    "must not rely on fixed shared paths (temp files, shared "
                    "result files, shared cwd writes) — a later trial can "
                    "clobber an earlier trial's inputs or outputs."
                ),
                "environment": (
                    "env_ref selects the interpreter when it names one: a "
                    "venv/conda directory (bin/python must exist) or an "
                    "executable file makes run_trial execute the wrapper "
                    "under it. Any other value (e.g. 'uv@0.10') is "
                    "provenance only and the server interpreter is used. "
                    "The applied interpreter is recorded in "
                    "executor_output.python_exe. Note: code_ref must still "
                    "expose run_training — capture the entrypoint file, "
                    "not a runner script that only works under the env."
                ),
                "isolation": {
                    "enforced": (
                        "cwd IS $ML_SCI_ARTIFACT_DIR (relative writes are "
                        "per-trial); TMPDIR = $ML_SCI_ARTIFACT_DIR/tmp "
                        "(tempfile users are per-trial automatically); "
                        "capture_bundle warns on hardcoded /tmp|/var/tmp|"
                        "/dev/shm literals in bundle code (warnings field)."
                    ),
                    "sandbox": (
                        "When bubblewrap is available, each trial runs in a "
                        "mount-namespace sandbox: 'minimal' gives a private "
                        "tmpfs /tmp (hardcoded shared-scratch paths become "
                        "harmless); 'full' mounts the root read-only so the "
                        "artifact dir is the only trial-private writable "
                        "location. Existing host caches (HF_HOME, "
                        "XDG_CACHE_HOME, TRITON_CACHE_DIR) are bind-mounted "
                        "rw at their real paths — they are lock-managed "
                        "shared state; a cold per-trial cache would "
                        "re-download multi-GB models every run. Caches with "
                        "no host dir get a per-trial redirect. Extra host "
                        "caches (e.g. ~/.cache/pip, ~/.cache/uv) can be "
                        "shared via [executor] shared_caches — bound if "
                        "the dir exists, reported under executor_output."
                        "shared_caches.missing if not. "
                        "Configure via [executor] sandbox = none|minimal|"
                        "full|auto in ml-episteme.toml or "
                        "ML_EPISTEME_SANDBOX (default auto = full when "
                        "bwrap exists). Isolation is REQUIRED: if a mode "
                        "needing bwrap is configured and bwrap is "
                        "missing, the run FAILS — it never silently "
                        "degrades. 'none' is the explicit opt-out."
                    ),
                    "provenance": (
                        "executor_output.sandbox records the isolation "
                        "actually applied (none|minimal|full) — check it "
                        "when assessing a trial's trustworthiness."
                    ),
                    "read_trace": (
                        "When strace is available, the trial's process "
                        "tree runs under `strace -f` INSIDE the sandbox; "
                        "the syscall log (reads + execs) is stored as a "
                        "trial artifact and every .py file actually "
                        "opened is content-addressed into code_snippets "
                        "with an executed_code.json manifest. This is "
                        "the authoritative record of what ran — the "
                        "bundle's static closure is the pre-run "
                        "prediction, the trace is the observation. "
                        "Configure via [executor] trace_reads = "
                        "auto|on|off (default auto; "
                        "executor_output.read_trace records which)."
                    ),
                    "seal_enforcement": (
                        "Each sealed snippet's bytes are staged to "
                        "$ML_SCI_ARTIFACT_DIR/_sealed/ and bind-mounted "
                        "OVER its original path inside the sandbox — the "
                        "code that runs is exactly what capture_bundle "
                        "hashed; post-seal edits or deletions of live "
                        "files cannot leak in. Files the run opens that "
                        "were never sealed still read live (flagged in "
                        "executed_code.json vs the bundle — surfaced, "
                        "not silent). executor_output.seal_enforced / "
                        "sealed_overlays record what applied; "
                        "sandbox=none cannot overlay and says so."
                    ),
                },
                "progress_reporting": {
                    "convention": (
                        "OPTIONAL: write $ML_SCI_ARTIFACT_DIR/progress.json "
                        "at reasonable intervals while running. get_trial_status "
                        "surfaces it as progress + eta_seconds."
                    ),
                    "env_vars": {
                        "ML_SCI_ARTIFACT_DIR": "the trial's artifact workspace",
                        "ML_SCI_TRIAL_ID": "the running trial's id",
                        "ML_SCI_PROGRAMME_ID": "the parent programme's id",
                    },
                    "schema": {
                        "pct": "float 0..1 — fraction complete",
                        "message": "str — human-readable phase (e.g. 'seed 2/3')",
                        "eta_seconds": (
                            "float — OPTIONAL, authoritative when present; "
                            "otherwise ETA is extrapolated from pct"
                        ),
                    },
                    "eta_semantics": (
                        "eta_seconds is the training code's own estimate when "
                        "provided, else elapsed*(1-pct)/pct. Absent when no "
                        "progress file exists — never fabricated."
                    ),
                },
                "example": {
                    "code_ref": "/path/to/train.py",
                    "train_py": (
                        "def run_training(config):\n"
                        "    seeds = config.get('seeds', [42])\n"
                        "    results = []\n"
                        "    for seed in seeds:\n"
                        "        # ... train with this seed ...\n"
                        "        results.append(val_perplexity)\n"
                        "    import statistics\n"
                        "    mean = statistics.mean(results)\n"
                        "    se = statistics.stdev(results) / len(results) ** 0.5\n"
                        "    return {\n"
                        "        'metrics': {'val_perplexity': mean},\n"
                        "        'variance': {'val_perplexity': se},\n"
                        "    }"
                    ),
                },
            },
            indent=2,
        )

    @mcp.resource("programme://{id}")
    def get_programme_resource(id: str) -> str:
        """The programme (goal, constraints, budget, status)."""
        p = store.get_programme(id)
        if p is None:
            return json.dumps({"error": "Programme not found"})
        return json.dumps(
            {
                "id": p.id,
                "goal": p.goal,
                "constraints": p.constraints,
                "allowed_variables": p.allowed_variables,
                "budget": {
                    "max_trials": p.budget_max_trials,
                    "max_wall_time_hours": p.budget_max_wall_time_hours,
                },
                "metric_direction": p.metric_direction,
                "status": p.status.value,
                "ontological_category": p.ontological_category,
                "aboutness": p.aboutness,
            },
            indent=2,
        )

    @mcp.resource("programme://{id}/hypotheses")
    def get_hypotheses_resource(id: str) -> str:
        """All hypotheses in the programme."""
        hyps = store.list_hypotheses(id)
        return json.dumps(
            [
                {
                    "id": h.id,
                    "statement": h.statement,
                    "failure_criterion": h.failure_criterion,
                    "status": h.status.value,
                }
                for h in hyps
            ],
            indent=2,
        )

    @mcp.resource("programme://{id}/trials")
    def get_trials_resource(id: str) -> str:
        """All trials with their configs and bundles."""
        trials = store.list_trials(id)
        return json.dumps(
            [
                {
                    "id": t.id,
                    "hypothesis_id": t.hypothesis_id,
                    "config": json.loads(t.config_json),
                    "bundle_id": t.bundle_id,
                    "status": t.status.value,
                    "created_at": t.created_at,
                    "started_at": t.started_at,
                    "finished_at": t.finished_at,
                    "duration_seconds": t.duration_seconds,
                }
                for t in trials
            ],
            indent=2,
        )

    @mcp.resource("programme://{id}/belief")
    def get_belief_resource(id: str) -> str:
        """The current belief state / posterior."""
        b = store.get_belief(id)
        if b is None:
            return json.dumps({"error": "No belief state found"})
        return json.dumps(
            {
                "id": b.id,
                "state": json.loads(b.state_json),
                "updated_at": b.updated_at,
            },
            indent=2,
        )

    @mcp.resource("programme://{id}/budget")
    def get_budget_resource(id: str) -> str:
        """Remaining budget (trials + time)."""
        p = store.get_programme(id)
        if p is None:
            return json.dumps({"error": "Programme not found"})
        trials = store.list_trials(id)
        remaining = p.budget_max_trials - len(trials)
        return json.dumps(
            {
                "max_trials": p.budget_max_trials,
                "used_trials": len(trials),
                "remaining_trials": remaining,
                "max_wall_time_hours": p.budget_max_wall_time_hours,
            },
            indent=2,
        )

    @mcp.resource("trial://{trial_id}/artifacts")
    def get_trial_artifacts(trial_id: str) -> str:
        """The artifact manifest for a trial.

        Prefers the captured SQLite artifacts (durable, survives workspace
        cleanup): filename, artifact_type, content_hash, size — fetch each
        file's content via artifact://{content_hash}.

        Falls back to the on-disk manifest for trials that predate
        artifact capture (run capture_pending_artifacts to migrate them).
        """
        trial = store.get_trial(trial_id)
        if trial is None:
            return json.dumps({"error": f"Trial not found: {trial_id}"})

        captured = store.list_trial_artifacts(trial_id)
        if captured:
            return json.dumps(
                {
                    "trial_id": trial_id,
                    "source": "sqlite",
                    "artifact_path": trial.artifact_path,
                    "artifacts": [
                        {
                            "filename": a["filename"],
                            "artifact_type": a["artifact_type"],
                            "content_hash": a["content_hash"],
                            "size_bytes": a["size_bytes"],
                            "content_type": a["content_type"],
                            "uri": f"artifact://{a['content_hash']}",
                        }
                        for a in captured
                    ],
                },
                indent=2,
            )

        if trial.artifact_path is None:
            return json.dumps({"error": f"Trial has no artifact workspace: {trial_id}"})

        artifact_dir = Path(trial.artifact_path)
        if not artifact_dir.exists():
            return json.dumps({"error": f"Artifact directory not found: {artifact_dir}"})

        # Find the most recent manifest (timestamped manifests)
        manifests = sorted(artifact_dir.glob("*_manifest.json"))
        if not manifests:
            # Fall back to un-timestamped manifest
            manifest_path = artifact_dir / "manifest.json"
            if manifest_path.exists():
                manifests = [manifest_path]

        if not manifests:
            return json.dumps({
                "trial_id": trial_id,
                "artifact_path": str(artifact_dir),
                "artifacts": [],
            })

        manifest_path = manifests[-1]
        with open(manifest_path) as f:
            manifest = json.load(f)

        return json.dumps(
            {
                "trial_id": trial_id,
                "source": "disk",
                "artifact_path": str(artifact_dir),
                "manifest": manifest,
            },
            indent=2,
        )

    @mcp.resource("artifact://{content_hash}")
    def get_artifact_content(content_hash: str) -> str:
        """A captured artifact file's content, by content hash.

        Serves the gzip-compressed BLOB stored at finalize time —
        stdout, stderr, wrapper script, result files. Text content is
        decoded UTF-8; binary content is returned base64-encoded.
        """
        af = store.get_artifact_file(content_hash)
        if af is None:
            return json.dumps({"error": f"Artifact not found: {content_hash}"})

        data = af["content"]
        result = {
            "content_hash": af["content_hash"],
            "filename": af["filename"],
            "content_type": af["content_type"],
            "size_bytes": af["size_bytes"],
            "captured_at": af["captured_at"],
            "original_path": af["original_path"],
        }
        try:
            result["encoding"] = "utf-8"
            result["content"] = data.decode("utf-8")
        except UnicodeDecodeError:
            import base64
            result["encoding"] = "base64"
            result["content_base64"] = base64.b64encode(data).decode("ascii")
        return json.dumps(result, indent=2)

    @mcp.resource("dataref://{data_ref_id}")
    def get_data_ref_resource(data_ref_id: str) -> str:
        """The DataRef details — provenance, hash, storage, risk.

        Returns the full DataRef record: regime (generated/captured),
        provenance (generator code/seed or source URI/version), content
        hash, storage URI, and reproducibility risk classification.
        """
        data_ref = store.get_data_ref(data_ref_id)
        if data_ref is None:
            return json.dumps({"error": f"DataRef not found: {data_ref_id}"})
        return json.dumps(
            {
                "id": data_ref.id,
                "split": data_ref.split,
                "regime": data_ref.regime,
                "generator_code_ref": data_ref.generator_code_ref,
                "generator_code_hash": data_ref.generator_code_hash,
                "generator_seed": data_ref.generator_seed,
                "generator_params": data_ref.generator_params,
                "source_uri": data_ref.source_uri,
                "version": data_ref.version,
                "capture_window_start": data_ref.capture_window_start,
                "capture_window_end": data_ref.capture_window_end,
                "capture_source_metadata": data_ref.capture_source_metadata,
                "content_hash": data_ref.content_hash,
                "schema_hash": data_ref.schema_hash,
                "size_bytes": data_ref.size_bytes,
                "num_samples": data_ref.num_samples,
                "storage_uri": data_ref.storage_uri,
                "reproducibility_risk": data_ref.reproducibility_risk,
                "created_at": data_ref.created_at,
                "ontological_category": data_ref.ontological_category,
                "aboutness": data_ref.aboutness,
            },
            indent=2,
        )

    @mcp.resource("code://{code_hash}")
    def get_code_snippet_resource(code_hash: str) -> str:
        """The captured code snippet — content-addressed source code.

        Returns the full code text stored at capture time. The code_hash
        is a SHA-256 content address: the same hash always returns the same
        code. This makes bundles self-contained — the code that ran is in
        the DB, not just a path to a file.
        """
        snippet = store.get_code_snippet(code_hash)
        if snippet is None:
            return json.dumps({"error": f"Code snippet not found: {code_hash}"})
        return json.dumps(
            {
                "code_hash": snippet.code_hash,
                "code_text": snippet.code_text,
                "language": snippet.language,
                "captured_at": snippet.captured_at,
                "original_path": snippet.original_path,
                "size_bytes": snippet.size_bytes,
                "ontological_category": snippet.ontological_category,
                "aboutness": snippet.aboutness,
            },
            indent=2,
        )

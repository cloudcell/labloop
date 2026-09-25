"""Trial tool handlers — design_experiment, capture_bundle, run_trial,
get_trial_status, cancel_trial, mark_retryable, list_trials."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from ..state.models import Bundle, Trial
from ..state.store import StateStore, lint_shared_paths
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    check_budget_exhausted,
    check_bundle_controlled,
    check_hypothesis_exists,
    check_hypothesis_in_programme,
    check_hypothesis_testable,
    check_loop_is_unit,
    check_programme_active,
    check_wall_time_exhausted,
)
from .schemas import coerce_int_list, coerce_json, fail, ok, CaptureBundleOut, DesignExperimentOut, ListTrialsOut, RunTrialOut, TrialStatusOut, CancelTrialOut, CaptureBundleFromCodeHashOut, CorrectTrialStatusOut, MarkRetryableOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


def _validate_code_ref(code_ref: str) -> str | None:
    """Validate that code_ref is a usable Python file path.

    Returns None if valid, or an error message.
    """
    if not code_ref:
        return "code_ref is empty"
    path = Path(code_ref)
    if not path.exists():
        return f"code_ref does not exist: {code_ref}"
    if path.suffix != ".py":
        return f"code_ref must be a .py file, got: {code_ref}"
    return None


def _stage_sealed_code(
    store: StateStore, bundle, artifact_dir: Path
) -> list[tuple[str, str]]:
    """Write every sealed snippet's bytes into artifact_dir/_sealed/
    and return (staged_path, original_path) overlay pairs.

    Inside the sandbox the executor bind-mounts each staged file over
    its original path, so the process reads exactly what capture_bundle
    hashed — post-seal edits or deletions of the live file cannot leak
    into the run. The staged bytes are hash-verified against the
    content address before dispatch.

    Only meaningful for file-path bundles (code:// bundles already
    materialize sealed snippets under _deps/ — original paths may not
    exist on a rerun host).
    """
    import hashlib as _h

    hashes = [bundle.code_hash] if bundle.code_hash else []
    if bundle.code_hash_extra_json:
        try:
            hashes += json.loads(bundle.code_hash_extra_json)
        except Exception:
            pass

    sealed_dir = artifact_dir / "_sealed"
    overlays: list[tuple[str, str]] = []
    seen_targets: set[str] = set()
    for h in hashes:
        cs = store.get_code_snippet(h)
        if cs is None or not cs.original_path:
            continue
        target = cs.original_path
        if target in seen_targets:
            continue
        seen_targets.add(target)
        # Integrity: the bytes we stage must hash to the content
        # address — a mismatch means the snippet store is corrupt.
        if "sha256:" + _h.sha256(cs.code_text.encode("utf-8")).hexdigest() != h:
            raise ValueError(
                f"Sealed snippet {h[:20]} failed hash verification "
                f"(original_path={target})"
            )
        staged = sealed_dir / f"{len(overlays):04d}_{Path(target).name}"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(cs.code_text, encoding="utf-8")
        overlays.append((str(staged), target))
    return overlays


def _dep_relpaths(original_path: str | None) -> list[str]:
    """Candidate module paths for materializing a snippet under _deps/.

    Derived from the captured file's trailing path components — covers
    `import ga`, `import mol.ga`, and `import src.mol.ga`-style imports
    (namespace packages resolve each level). Capped at 3 components.
    """
    if not original_path:
        return []
    parts = Path(original_path).parts
    name = parts[-1]
    relpaths = [name]
    if len(parts) >= 3:
        relpaths.append(str(Path(parts[-2]) / name))
    if len(parts) >= 4:
        relpaths.append(str(Path(parts[-3]) / parts[-2] / name))
    return relpaths


def _lint_bundle_snippets(store: StateStore, code_hashes: list[str]) -> list[dict]:
    """Advisory lint over captured snippets for hardcoded shared-scratch
    paths (concurrent trials can clobber each other's /tmp files)."""
    warnings: list[dict] = []
    for h in code_hashes:
        snippet = store.get_code_snippet(h)
        if snippet is None:
            continue
        warnings.extend(
            lint_shared_paths(snippet.code_text, snippet.original_path or h)
        )
    return warnings


def _resolve_env_python(env_ref: str | None) -> str | None:
    """Resolve a bundle's env_ref to a concrete Python interpreter.

    env_ref is free-form provenance, but when it names a filesystem
    environment — a venv/conda dir containing ``bin/python``, or an
    executable file — it becomes the trial's interpreter (the
    declared env is what actually runs the code). Non-path values
    like ``uv@0.10`` and unresolvable paths leave the executor's
    default interpreter.
    """
    if not env_ref:
        return None
    p = Path(env_ref)
    if p.is_dir():
        for cand in (p / "bin" / "python", p / "bin" / "python3"):
            if cand.exists():
                return str(cand)
        return None
    if p.is_file() and os.access(p, os.X_OK):
        return str(p)
    return None


def _materialize_plan(
    dep_snippets: list[tuple[str | None, str]],
) -> dict[str, str]:
    """relpath → code_text for every dep snippet's candidate paths.

    If two snippets produce the same relpath with different content,
    that name is dropped entirely — a clean ImportError beats silently
    binding the wrong module.
    """
    plan: dict[str, str | None] = {}
    for original_path, text in dep_snippets:
        for rel in _dep_relpaths(original_path):
            existing = plan.get(rel)
            if existing is None and rel in plan:
                continue  # already poisoned
            if existing is not None and existing != text:
                plan[rel] = None  # ambiguous name — poison it
            else:
                plan[rel] = text
    return {r: t for r, t in plan.items() if t is not None}


def _generate_execution_wrapper(
    trial_id: str, config: dict, code_ref: str, code_text: str | None = None,
    dep_snippets: list[tuple[str | None, str]] | None = None,
) -> str:
    """Generate a Python wrapper script that imports and calls run_training.

    Contract:
    - code_ref is a path to a Python file exposing `def run_training(config: dict) -> dict`
    - run_training returns {"metrics": {...}, "variance": {...}}
    - The wrapper prints the result as JSON to stdout

    If code_text is provided (content-addressed code from code_snippets),
    the code is inlined directly into the wrapper instead of imported
    from a file. This is the rerun-from-archive path: no filesystem
    dependency.

    If dep_snippets is provided (multi-file capture: (original_path,
    code_text) pairs), the wrapper MATERIALIZES them as real files
    under a `_deps/` dir next to itself — reconstructing path suffixes
    (stem, last-2, last-3 components) — and puts `_deps` on sys.path.
    Real import machinery then resolves `from src.trainer import x`
    and `import mol.ga` inside the primary code; inlining dependency
    text cannot (it only satisfies bare-name references).

    If code_text is None and code_ref is a valid .py file path, the
    wrapper imports from the file (existing behavior).
    """
    config_json = json.dumps(config)

    # --- Content-addressed code: inline from code_snippets ---
    if code_text is not None:
        deps_block = ""
        if dep_snippets:
            plan = _materialize_plan(dep_snippets)
            if plan:
                deps_block = (
                    f"import os, tempfile\n"
                    f"try:\n"
                    f"    _base = os.path.dirname(os.path.abspath(__file__))\n"
                    f"except NameError:\n"
                    f"    _base = tempfile.mkdtemp(prefix='ml_sci_')\n"
                    f"_deps_dir = os.path.join(_base, '_deps')\n"
                    f"_deps_files = {repr(plan)}\n"
                    f"for _rel, _src in _deps_files.items():\n"
                    f"    _p = os.path.join(_deps_dir, _rel)\n"
                    f"    os.makedirs(os.path.dirname(_p), exist_ok=True)\n"
                    f"    with open(_p, 'w') as _f:\n"
                    f"        _f.write(_src)\n"
                    f"if _deps_dir not in sys.path:\n"
                    f"    sys.path.insert(0, _deps_dir)\n"
                    f"\n"
                )
        return (
            f"# Trial {trial_id}\n"
            f"# Config: {config_json}\n"
            f"# Code ref: {code_ref}\n"
            f"# Code inlined from code_snippets (content-addressed)\n"
            f"import json, sys\n"
            f"\n"
            f"{deps_block}"
            f"# --- Primary module (inlined) ---\n"
            f"{code_text}\n"
            f"\n"
            f"if 'run_training' not in dir():\n"
            f"    print(json.dumps({{'error': 'code does not expose run_training(config) -> dict', 'code_ref': {repr(code_ref)}}}))\n"
            f"    sys.exit(1)\n"
            f"\n"
            f"config = {repr(config)}\n"
            f"result = run_training(config)\n"
            f"print(json.dumps(result))\n"
        )

    # --- File-path code: import from file (existing behavior) ---
    # Check if code_ref is a file path we can import from
    code_path = Path(code_ref) if code_ref else None
    is_file = code_path is not None and code_path.exists() and code_path.suffix == ".py"

    if is_file:
        # Import run_training from the file
        module_path = str(code_path)
        module_dir = str(code_path.parent)
        return (
            f"# Trial {trial_id}\n"
            f"# Config: {config_json}\n"
            f"# Code ref: {code_ref}\n"
            f"import json, sys, importlib.util, os\n"
            f"\n"
            f"# Add the executor's directory to sys.path so local imports\n"
            f"# (e.g. 'from utils import compute_metric') resolve correctly.\n"
            f"# This is essential for multi-file executors (commitment 2 + 5).\n"
            f"_executor_dir = {repr(module_dir)}\n"
            f"if _executor_dir not in sys.path:\n"
            f"    sys.path.insert(0, _executor_dir)\n"
            f"\n"
            f"# Load the user's training module\n"
            f"spec = importlib.util.spec_from_file_location('user_training', {repr(module_path)})\n"
            f"module = importlib.util.module_from_spec(spec)\n"
            f"spec.loader.exec_module(module)\n"
            f"\n"
            f"if not hasattr(module, 'run_training'):\n"
            f"    print(json.dumps({{'error': 'code_ref does not expose run_training(config) -> dict', 'code_ref': {repr(code_ref)}}}))\n"
            f"    sys.exit(1)\n"
            f"\n"
            f"config = {repr(config)}\n"
            f"result = module.run_training(config)\n"
            f"print(json.dumps(result))\n"
        )
    else:
        # code_ref is not a usable file path — explain the contract
        return (
            f"# Trial {trial_id}\n"
            f"# Config: {config_json}\n"
            f"# Code ref: {code_ref}\n"
            f"import json, sys\n"
            f"\n"
            f"error_msg = (\n"
            f"    'run_trial failed: bundle.code_ref must point to a Python file '\n"
            f"    'that exposes def run_training(config: dict) -> dict. '\n"
            f"    'Got code_ref={repr(code_ref)} which is not a valid .py file path. '\n"
            f"    'In capture_bundle, set code_ref to the path of your training script. '\n"
            f"    'The script must define run_training(config) returning '\n"
            f"    '\\\"metrics\\\": {{...}}, \\\"variance\\\": {{...}}\\\".'\n"
            f")\n"
            f"print(json.dumps({{'error': error_msg, 'code_ref': {repr(code_ref)}}}))\n"
            f"sys.exit(1)\n"
        )


def _parse_executor_output(output: str) -> dict:
    """Parse executor output JSON. Returns dict with at least 'status'."""
    try:
        data = json.loads(output) if isinstance(output, str) else {"raw": output}
        if not isinstance(data, dict):
            return {"status": "completed", "raw": output}
        return data
    except (json.JSONDecodeError, TypeError):
        return {"status": "completed", "raw": output}


def _last_json_line(text: str) -> dict | None:
    """Last JSON dict in a possibly multi-line stdout — the wrapper
    prints run_training's result last; noise may precede it."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def _finalize_trial(store: StateStore, trial_id: str, output: str) -> CallToolResult:
    """Parse executor output, update trial status, and return the result.

    If the executor reports failure (status != completed, nonzero exit),
    the trial is marked 'failed'. Otherwise 'completed'.

    Idempotent: if the trial is already in a terminal state (completed or
    failed), the status is not updated again. This allows both the
    background auto-finalizer and get_trial_status to finalize the same
    trial without raising an illegal-transition error.
    """
    output_data = _parse_executor_output(output)

    # Persist the raw output — the executor's async cache is in-memory
    # and dies on restart; the trial row is the durable record.
    trial = store.get_trial(trial_id)
    if trial is not None and output and trial.executor_output_json != output:
        try:
            store.update_trial_executor_output(trial_id, output)
        except Exception:
            pass  # never let output persistence mask finalization

    # Check if trial is already terminal (idempotent finalization)
    if trial is not None and trial.status.value in ("completed", "failed", "retryable"):
        return ok({
            "trial_id": trial_id,
            "status": trial.status.value,
            "executor_output": output,
        })

    # Check for failure
    status = output_data.get("status", "completed")
    exit_code = output_data.get("exit_code", 0)
    has_error = "error" in output_data

    # The outer wrapper can exit 0 while the inner job crashed — a
    # run_training that returns {"status": "error", ...} instead of
    # raising. Parse the recorded stdout payload for the inner verdict.
    inner_error = None
    stdout = output_data.get("stdout")
    if isinstance(stdout, str):
        inner = _last_json_line(stdout)
        if isinstance(inner, dict):
            inner_rc = inner.get("_exit_code") or inner.get("exit_code")
            if inner.get("status") == "error" or inner.get("error") or inner_rc:
                inner_error = (
                    inner.get("error")
                    or (inner.get("_stderr_tail") or "")[-500:]
                    or "inner job reported failure"
                )

    if status != "completed" or exit_code != 0 or has_error or inner_error:
        store.update_trial_status(trial_id, "failed")
        # Record duration if available
        duration = output_data.get("duration_seconds")
        if duration is not None:
            store.update_trial_duration(trial_id, duration)

        # Capture artifacts even for failed trials (logs are evidence of failure)
        artifact_result = None
        if trial is not None and trial.artifact_path:
            try:
                store.capture_executed_code(trial_id, trial.artifact_path)
            except Exception:
                pass  # evidence capture must never mask finalization
            try:
                artifact_result = store.capture_artifacts_from_dir(
                    trial_id, trial.artifact_path
                )
            except Exception as e:
                artifact_result = {"error": str(e)}

        result = {
            "trial_id": trial_id,
            "status": "failed",
            "executor_output": output,
            "error": output_data.get(
                "error", inner_error or "Execution failed"
            ),
        }
        if artifact_result is not None:
            result["artifacts"] = artifact_result
        return ok(result)

    # 'completed' is a claim that the trial ran and produced a recorded
    # result — the store refuses it without an executor record. An
    # executor that returned nothing recordable is a failure, not a
    # completion.
    from ..state.store import _has_executor_record
    if not _has_executor_record(output if isinstance(output, str) else None):
        store.update_trial_status(trial_id, "failed")
        return ok({
            "trial_id": trial_id,
            "status": "failed",
            "executor_output": output,
            "error": "executor produced no recordable output — "
                     "a completion without an executor record is "
                     "unevidenced",
        })

    store.update_trial_status(trial_id, "completed")
    # Record duration if available
    duration = output_data.get("duration_seconds")
    if duration is not None:
        store.update_trial_duration(trial_id, duration)

    # Capture artifacts from the artifact directory into SQLite
    # (gzip-compressed). Executed-code capture runs first so the
    # manifest it writes is captured as a trial artifact too.
    artifact_result = None
    if trial is not None and trial.artifact_path:
        try:
            store.capture_executed_code(trial_id, trial.artifact_path)
        except Exception:
            pass
        try:
            artifact_result = store.capture_artifacts_from_dir(
                trial_id, trial.artifact_path
            )
        except Exception as e:
            artifact_result = {"error": str(e)}

    result = {
        "trial_id": trial_id,
        "status": "completed",
        "executor_output": output,
    }
    if artifact_result is not None:
        result["artifacts"] = artifact_result
    return ok(result)


async def _auto_finalize(
    trial_id: str, store: StateStore, adaptor: MCPAdaptor,
    poll_seconds: float = 5.0,
) -> None:
    """Background task: poll the executor and finalize the trial when it
    completes.

    This runs after run_trial dispatches an async job and returns
    "running" to the client. It polls the executor every
    `poll_seconds` ([executor] finalize_poll_seconds) and
    finalizes the trial (marks it completed/failed in the store) when the
    executor reports a terminal status. This means the trial is
    finalized automatically even if the client never calls
    get_trial_status — the client doesn't need polling capabilities to
    close the loop.

    The task is fire-and-forget: errors are logged to the trial status
    (marked failed) but not raised. _finalize_trial is idempotent, so
    racing with get_trial_status is safe.
    """
    try:
        while True:
            await asyncio.sleep(poll_seconds)
            async_output = adaptor.executor.get_async_status(trial_id)
            async_data = _parse_executor_output(async_output)
            if async_data.get("status") in ("completed", "failed", "timeout"):
                _finalize_trial(store, trial_id, async_output)
                return
    except Exception:
        # If the poller itself fails (infrastructure issue — executor
        # state lost, server restart, etc.), mark the trial as
        # retryable — distinct from scientific failure.
        try:
            trial = store.get_trial(trial_id)
            if trial is not None and trial.status.value not in ("completed", "failed", "retryable"):
                store.update_trial_status(trial_id, "retryable")
        except Exception:
            pass


def register(
    mcp, store: StateStore, adaptor: MCPAdaptor,
    executor_config: dict | None = None,
) -> None:
    """Register trial-related tools on the MCP server.

    executor_config is the [executor] table: submit_wait_seconds bounds
    the synchronous window before a trial goes async;
    finalize_poll_seconds is the auto-finalizer's poll cadence. Both
    are server internals — never client-settable.
    """
    _exec_cfg = executor_config or {}
    _submit_wait = float(_exec_cfg.get("submit_wait_seconds", 10.0))
    _finalize_poll = float(_exec_cfg.get("finalize_poll_seconds", 5.0))

    @mcp.tool()
    def design_experiment(programme_id: Annotated[str, Field(description='ID of the target research programme.')], hypothesis_id: Annotated[str, Field(description='ID of the target hypothesis.')], config: Annotated[dict | str, Field(description='Trial configuration dict handed to run_training; may be JSON-encoded.')]) -> Annotated[CallToolResult, DesignExperimentOut]:
        """Design an experiment: create a trial (data item) within the programme.

        config may be sent as a JSON-encoded string if your client
        cannot emit objects.

        Enforcement: commitment 5 — budget is an epistemic resource (trial count + wall time).
        Enforcement: commitment 10 — the agent is a scientist (hypothesis must exist).
        """
        try:
            config = coerce_json(config, dict, "config")
            # Enforcement: commitment 10 — the agent is a scientist, not a scribe
            hyp = store.get_hypothesis(hypothesis_id)
            err = check_hypothesis_exists(hyp)
            if err:
                return fail(json.dumps({"error": err}))
            err = check_hypothesis_testable(hyp)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — a closed programme is immutable
            programme = store.get_programme(programme_id)
            err = check_programme_active(programme)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 10 — hypothesis must belong to this
            # programme (else it can never be concluded: zombie hypothesis)
            err = check_hypothesis_in_programme(
                programme_id, hypothesis_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 5 — budget is an epistemic resource (trial count)
            trials = store.list_trials(programme_id)
            err = check_budget_exhausted(programme, trials)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 5 — budget is an epistemic resource (wall time)
            err = check_wall_time_exhausted(programme, trials)
            if err:
                return fail(json.dumps({"error": err}))

            trial = Trial(
                id=f"trial-{uuid.uuid4().hex[:8]}",
                programme_id=programme_id,
                hypothesis_id=hypothesis_id,
                config_json=json.dumps(config),
            )
            store.create_trial(trial)

            # Set hypothesis to under_test (legal transition: proposed →
            # under_test). Atomic + idempotent — parallel design calls
            # race on the stale `hyp` snapshot above; the loser must not
            # fail AFTER its trial row exists.
            store.promote_hypothesis_if_proposed(hypothesis_id)

            return ok({"trial_id": trial.id, "status": "designed"})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def capture_bundle(
        trial_id: Annotated[str, Field(description='ID of the target trial.')],
        code_ref: Annotated[str, Field(description='Path to a Python file exposing run_training(config: dict) -> dict returning {"metrics": {...}, "variance": {...}} — see executor://contract.')],
        env_ref: Annotated[str, Field(description='Environment reference sealed into the bundle (pre-registration).')],
        seeds: Annotated[list[int] | int | str, Field(description='Seed set sealed into the bundle (pre-registration — fixes the assumption before any observation); list or JSON-encoded.')],
        splits: Annotated[dict | str, Field(description='Split spec sealed into the bundle — which data each split used; object or JSON-encoded.')],
        baseline_ref: Annotated[str | None, Field(description='Reference to the baseline the trial compares against.')] = None,
        data_refs: Annotated[list[str] | str | None, Field(description='DataRef IDs from prepare_data — structured data provenance (falls back to splits when omitted); list or JSON-encoded.')] = None,
        extra_code_refs: Annotated[list[str] | str | None, Field(description='Additional code files to seal into the bundle; list or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, CaptureBundleOut]:
        """Seal the auxiliary bundle for a DESIGNED trial (commitment 6).

        ORDERING: call AFTER design_experiment and BEFORE run_trial.
        The seal is pre-registration — the bundle fixes the auxiliary
        assumptions (code/env/seeds/splits) before any observation, so
        they cannot be retro-fitted to results. Trials that have left
        'designed' are rejected. The post-run counterpart is
        executed_code.json — what actually ran, captured at
        finalization from the strace read-trace.

        code_ref MUST be a path to a Python file that exposes:
            def run_training(config: dict) -> dict
        returning {"metrics": {...}, "variance": {...}}.
        The config is the same dict passed to design_experiment.
        Read the executor://contract resource for the full contract.

        data_refs is an optional list of DataRef IDs (from prepare_data).
        When provided, the bundle records structured data provenance.
        When omitted, splits is used (backward-compatible).

        extra_code_refs is an optional list of additional .py file paths
        that the trial depends on but cannot be discovered by AST import
        analysis — e.g. scripts invoked via subprocess.run(). These are
        captured into code_snippets and stored in code_hash_extra_json so
        the bundle is fully self-contained and rerunnable from archive
        (commitment 2 + 5 — the bundle must contain ALL code needed to
        reproduce, not just the executor).

        Enforcement: commitment 1 — the loop is the unit (trial must exist).
        Enforcement: commitment 6 — the bundle must be controlled (code_ref validated).
        Concurrency: rejects if trial already has a bundle (no double capture).

        Structured params (seeds, splits, data_refs, extra_code_refs) may
        be sent as JSON-encoded strings; seeds also accepts a bare int.
        """
        try:
            seeds = coerce_int_list(seeds, "seeds")
            splits = coerce_json(splits, dict, "splits")
            if data_refs is not None:
                data_refs = coerce_json(data_refs, list, "data_refs")
            if extra_code_refs is not None:
                extra_code_refs = coerce_json(extra_code_refs, list, "extra_code_refs")
            # Enforcement: commitment 1 — trial must exist (no orphan bundles)
            trial = store.get_trial(trial_id)
            if trial is None:
                return fail(json.dumps({"error": f"Trial not found: {trial_id}"}))

            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(
                store.get_programme(trial.programme_id)
            )
            if err:
                return fail(json.dumps({"error": err}))

            # Concurrency: reject double capture (logical race prevention)
            if trial.bundle_id is not None:
                return fail(json.dumps({
                    "error": f"Trial {trial_id} already has a bundle: {trial.bundle_id}",
                }))

            # Enforcement: the seal is pre-registration — a trial that
            # has left 'designed' has already been (or is being)
            # executed, so capturing now would seal auxiliaries after
            # the fact. Today this is unreachable (run_trial requires a
            # bundle, so non-designed trials all fail the double-capture
            # check above) — the guard makes the ordering explicit and
            # holds if a future path ever lets a bundle-less trial run.
            if trial.status.value != "designed":
                return fail(json.dumps({
                    "error": (
                        f"capture_bundle requires a designed trial — "
                        f"{trial_id} is {trial.status.value}. The bundle "
                        "is sealed BEFORE execution (pre-registration); "
                        "executed code is captured separately at "
                        "finalization (executed_code.json)."
                    ),
                }))

            # Enforcement: commitment 6 — validate code_ref at capture time
            err = _validate_code_ref(code_ref)
            if err:
                return fail(json.dumps({"error": err}))

            # Validate extra_code_refs (each must be an existing .py file)
            if extra_code_refs:
                for extra_ref in extra_code_refs:
                    err = _validate_code_ref(extra_ref)
                    if err:
                        return fail(json.dumps({
                            "error": f"Invalid extra_code_ref: {err}"
                        }))

            # Phase 0: capture code content (content-addressed)
            # Read the file, compute SHA-256, store in code_snippets.
            # This makes the bundle self-contained: the code survives
            # even if the file is moved or deleted.
            # Multi-file capture: also capture locally-imported modules
            # recursively (commitment 2 + 5 — the bundle must contain ALL
            # code needed to reproduce, not just the executor).
            try:
                code_hash, code_hash_extra = store.capture_code_with_imports(code_ref)
            except Exception as e:
                return fail(json.dumps({"error": f"Failed to capture code: {e}"}))

            # Explicit extra_code_refs: capture each and add to code_hash_extra.
            # These are for files the trial depends on but that cannot be
            # discovered by AST import analysis (e.g. subprocess-dispatched
            # scripts). The caller declares them explicitly.
            if extra_code_refs:
                for extra_ref in extra_code_refs:
                    try:
                        extra_hash, extra_sub_hashes = (
                            store.capture_code_with_imports(extra_ref)
                        )
                        if extra_hash not in code_hash_extra:
                            code_hash_extra.append(extra_hash)
                        for sh in extra_sub_hashes:
                            if sh not in code_hash_extra:
                                code_hash_extra.append(sh)
                    except Exception as e:
                        return fail(json.dumps({
                            "error": f"Failed to capture extra_code_ref {extra_ref}: {e}"
                        }))

            # Validate data_refs if provided
            data_refs_json = None
            if data_refs is not None:
                for ref_id in data_refs:
                    ref = store.get_data_ref(ref_id)
                    if ref is None:
                        return fail(json.dumps({
                            "error": f"DataRef not found: {ref_id}. "
                            f"Call prepare_data first to create a DataRef.",
                        }))
                data_refs_json = json.dumps(data_refs)

            bundle = Bundle(
                id=f"bundle-{uuid.uuid4().hex[:8]}",
                trial_id=trial_id,
                code_ref=code_ref,
                code_hash=code_hash,
                code_hash_extra_json=json.dumps(code_hash_extra) if code_hash_extra else None,
                env_ref=env_ref,
                seeds_json=json.dumps(seeds),
                splits_json=json.dumps(splits),
                data_refs_json=data_refs_json,
                baseline_ref=baseline_ref,
            )
            store.create_bundle(bundle)
            store.link_bundle(trial_id, bundle.id)
            return ok({
                "bundle_id": bundle.id,
                "status": "captured",
                "code_hash": code_hash,
                "code_hash_extra": code_hash_extra,
                "warnings": _lint_bundle_snippets(
                    store, [code_hash] + code_hash_extra
                ),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def capture_bundle_from_code_hash(
        trial_id: Annotated[str, Field(description='ID of the target trial.')],
        code_hash: Annotated[str, Field(description="'sha256:...' content address from a prior capture_bundle — NOT a bundle_id; must already exist in code_snippets.")],
        env_ref: Annotated[str, Field(description='Environment reference sealed into the bundle (pre-registration).')],
        seeds: Annotated[list[int] | int | str, Field(description='Seed set sealed into the bundle (pre-registration); list or JSON-encoded.')],
        splits: Annotated[dict | str, Field(description='Split spec sealed into the bundle; object or JSON-encoded.')],
        baseline_ref: Annotated[str | None, Field(description='Reference to the baseline the trial compares against.')] = None,
        data_refs: Annotated[list[str] | str | None, Field(description='DataRef IDs from prepare_data — structured data provenance; list or JSON-encoded.')] = None,
        code_hash_extra: Annotated[list[str] | str | None, Field(description='Content hashes of additional code files to seal; list or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, CaptureBundleFromCodeHashOut]:
        """Capture a bundle using a content address (code_hash) instead of a file path.

        This is the rerun-from-archive path: the LLM reads an archived
        programme, gets the code_hash from the bundle, and captures a
        new bundle for a new trial using the same code content. No
        filesystem access is required — the code is loaded from
        code_snippets by hash and inlined into the execution wrapper
        at run_trial time.

        NOTE: code_hash is a "sha256:..." content address returned by a
        prior capture_bundle — it is NOT a bundle_id. To re-use an
        existing bundle's code, pass its code_hash field.

        The code_hash must already exist in code_snippets (captured by
        a prior capture_bundle or prepare_data call). If it doesn't,
        the tool returns an error.

        The bundle's code_ref is set to "code://{code_hash}" — a
        content address, not a file path. This is the carrier/content
        separation (Rule 5.4) made explicit: the bundle references the
        ICE directly.

        code_hash_extra is an optional list of additional content
        addresses for locally-imported or subprocess-dispatched modules
        captured alongside the primary. Each must already exist in
        code_snippets. At run_trial time, these are materialized as real
        files under a `_deps/` dir on sys.path, reconstructing each
        file's path suffix — so `from src.mod import x` resolves through
        the real import machinery (commitment 2 + 5).

        All other parameters are identical to capture_bundle.

        Structured params (seeds, splits, data_refs, code_hash_extra) may
        be sent as JSON-encoded strings; seeds also accepts a bare int.

        Returns: {"bundle_id": "bundle-...", "status": "captured", "code_hash": ...}
        """
        try:
            seeds = coerce_int_list(seeds, "seeds")
            splits = coerce_json(splits, dict, "splits")
            if data_refs is not None:
                data_refs = coerce_json(data_refs, list, "data_refs")
            if code_hash_extra is not None:
                code_hash_extra = coerce_json(code_hash_extra, list, "code_hash_extra")
            # Enforcement: trial must exist
            trial = store.get_trial(trial_id)
            if trial is None:
                return fail(json.dumps({"error": f"Trial not found: {trial_id}"}))

            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(
                store.get_programme(trial.programme_id)
            )
            if err:
                return fail(json.dumps({"error": err}))

            # Concurrency: reject double capture
            if trial.bundle_id is not None:
                return fail(json.dumps({
                    "error": f"Trial {trial_id} already has a bundle: {trial.bundle_id}",
                }))

            # Same pre-registration guard as capture_bundle.
            if trial.status.value != "designed":
                return fail(json.dumps({
                    "error": (
                        f"capture_bundle_from_code_hash requires a designed "
                        f"trial — {trial_id} is {trial.status.value}."
                    ),
                }))

            # Validate code_hash — promotion path: if the hash is not
            # in code_snippets, an ingested artifact (artifact_files)
            # with the same content address is materialized into a
            # snippet. Remote agents push bytes via the ingest surface
            # and reference the returned hash here.
            try:
                snippet = store.promote_artifact_to_snippet(code_hash)
            except ValueError as e:
                return fail(json.dumps({"error": str(e)}))
            if snippet is None:
                return fail(json.dumps({
                    "error": f"Code snippet not found for code_hash: {code_hash}. "
                    f"The code must be captured first (via capture_bundle, "
                    f"prepare_data, or the artifact-ingest surface) before "
                    f"it can be referenced by hash.",
                }))

            # Validate code_hash_extra: same promotion path per hash
            if code_hash_extra:
                for eh in code_hash_extra:
                    try:
                        extra_snippet = store.promote_artifact_to_snippet(eh)
                    except ValueError as e:
                        return fail(json.dumps({"error": str(e)}))
                    if extra_snippet is None:
                        return fail(json.dumps({
                            "error": f"Code snippet not found for extra code_hash: {eh}. "
                            f"All extra hashes must be captured or ingested first.",
                        }))

            # Validate data_refs if provided
            data_refs_json = None
            if data_refs is not None:
                for ref_id in data_refs:
                    ref = store.get_data_ref(ref_id)
                    if ref is None:
                        return fail(json.dumps({
                            "error": f"DataRef not found: {ref_id}. "
                            f"Call prepare_data first to create a DataRef.",
                        }))
                data_refs_json = json.dumps(data_refs)

            # Create the bundle with a content address as code_ref
            bundle = Bundle(
                id=f"bundle-{uuid.uuid4().hex[:8]}",
                trial_id=trial_id,
                code_ref=f"code://{code_hash}",
                code_hash=code_hash,
                code_hash_extra_json=json.dumps(code_hash_extra) if code_hash_extra else None,
                env_ref=env_ref,
                seeds_json=json.dumps(seeds),
                splits_json=json.dumps(splits),
                data_refs_json=data_refs_json,
                baseline_ref=baseline_ref,
            )
            store.create_bundle(bundle)
            store.link_bundle(trial_id, bundle.id)
            return ok({
                "bundle_id": bundle.id,
                "status": "captured",
                "code_hash": code_hash,
                "code_hash_extra": code_hash_extra or [],
                "code_ref": f"code://{code_hash}",
                "warnings": _lint_bundle_snippets(
                    store, [code_hash] + (code_hash_extra or [])
                ),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def run_trial(programme_id: Annotated[str, Field(description='ID of the programme owning the trial (orphan check).')], trial_id: Annotated[str, Field(description='ID of the target trial.')]) -> Annotated[CallToolResult, RunTrialOut]:
        """Run a trial by calling the executor role.

        Imports run_training from the bundle's code_ref and calls it with
        the trial config. The code_ref must be a path to a .py file exposing
        def run_training(config: dict) -> dict returning
        {"metrics": {...}, "variance": {...}}.
        Read the executor://contract resource for the full contract.

        For long-running jobs, this returns quickly with status "running".
        Use get_trial_status to poll for completion.

        Enforcement: commitment 6 — the bundle must be controlled.
        Rejects if the bundle is not fully captured.
        Concurrency: rejects if trial is already running (no double execution).
        """
        try:
            # Enforcement: commitment 1 — the loop is the unit (orphan runs forbidden)
            trial = store.get_trial(trial_id)
            err = check_loop_is_unit(trial, programme_id)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(
                store.get_programme(programme_id)
            )
            if err:
                return fail(json.dumps({"error": err}))

            # Concurrency: reject double execution (logical race prevention)
            if trial.status.value == "running":
                return fail(json.dumps({
                    "error": f"Trial {trial_id} is already running. Use get_trial_status to poll.",
                }))

            # Reject re-running a terminal trial (completed/failed/retryable are terminal)
            if trial.status.value in ("completed", "failed", "retryable"):
                return fail(json.dumps({
                    "error": (
                        f"Trial {trial_id} is already {trial.status.value} "
                        f"(terminal state). Re-running a finished trial is "
                        f"not allowed — it would duplicate evidence. "
                        f"Design a new trial with design_experiment instead."
                    ),
                }))

            # Enforcement: commitment 6 — the bundle must be controlled
            err = check_bundle_controlled(trial, store)
            if err:
                return fail(json.dumps({"error": err}))

            store.update_trial_status(trial_id, "running")

            # Generate the execution wrapper.
            config = json.loads(trial.config_json)
            bundle = store.get_bundle(trial.bundle_id)

            # Resolve DataRefs to read-only paths and inject into config
            if bundle.data_refs_json:
                data_ref_ids = json.loads(bundle.data_refs_json)
                data_paths = {}
                for ref_id in data_ref_ids:
                    try:
                        path = await adaptor.data_source.resolve_data_path(ref_id)
                        ref = store.get_data_ref(ref_id)
                        if ref is not None:
                            data_paths[ref.split] = path
                    except Exception as e:
                        return fail(json.dumps({
                            "error": f"Failed to resolve DataRef {ref_id}: {e}",
                        }))
                config["data_paths"] = data_paths

            # Resolve code_ref: if it's a code:// URI, inline from code_snippets
            code_text = None
            dep_snippets: list[tuple[str | None, str]] = []
            if bundle.code_ref and bundle.code_ref.startswith("code://"):
                code_hash = bundle.code_ref[len("code://"):]
                snippet = store.get_code_snippet(code_hash)
                if snippet is None:
                    return fail(json.dumps({
                        "error": f"Code snippet not found for code_hash: {code_hash}. "
                        f"The bundle references code that was not captured.",
                    }))
                code_text = snippet.code_text

                # The primary is materialized too so deps can import it
                # by name (e.g. `import executor`).
                dep_snippets.append((snippet.original_path, snippet.code_text))

                # Multi-file: also load extra code snippets (local imports).
                # Each carries its original_path so the wrapper can
                # reconstruct package layout under _deps/ and let real
                # import machinery resolve `from src.x import y`.
                if bundle.code_hash_extra_json:
                    try:
                        extra_hashes = json.loads(bundle.code_hash_extra_json)
                    except Exception:
                        extra_hashes = []
                    for eh in extra_hashes:
                        extra_snippet = store.get_code_snippet(eh)
                        if extra_snippet is not None:
                            dep_snippets.append(
                                (extra_snippet.original_path, extra_snippet.code_text)
                            )

            execution_code = _generate_execution_wrapper(
                trial_id, config, bundle.code_ref,
                code_text=code_text,
                dep_snippets=dep_snippets,
            )

            # Create the artifact workspace directory.
            artifact_dir = Path(store.state_dir) / "artifacts" / programme_id / trial_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
            store.update_trial_artifact_path(trial_id, str(artifact_dir))

            # Host paths the sandbox must re-bind inside the private
            # /tmp view: the code_ref the wrapper imports from (file-path
            # bundles) and any resolved data paths in the config.
            extra_ro_paths = []
            if not bundle.code_ref.startswith("code://"):
                extra_ro_paths.append(bundle.code_ref)
            for p in (config.get("data_paths") or {}).values():
                if isinstance(p, str):
                    extra_ro_paths.append(p)

            # Seal enforcement: stage every sealed snippet's bytes and
            # overlay them over their original paths inside the sandbox,
            # so the code that runs is exactly what capture_bundle
            # hashed — post-seal edits/deletions of the live files
            # cannot leak in. Unsealed code the run still touches (e.g.
            # dynamically-imported deps outside the closure) reads the
            # live file and is flagged by the executed_code.json
            # divergence check — surfaced, not silent.
            overlay_ro: list[tuple[str, str]] = []
            if not bundle.code_ref.startswith("code://"):
                try:
                    overlay_ro = _stage_sealed_code(store, bundle, artifact_dir)
                except Exception as e:
                    store.update_trial_status(trial_id, "failed")
                    return fail(json.dumps({
                        "error": f"Failed to stage sealed bundle code: {e}",
                    }))

            # env_ref is load-bearing: a venv/conda dir or executable
            # selects the interpreter the wrapper actually runs under.
            python_exe = _resolve_env_python(bundle.env_ref)
            if python_exe is not None:
                # Bind the env root, not just the binary — a venv's
                # bin/python needs ../lib (site-packages, pyvenv.cfg)
                # visible inside the private tmpfs.
                extra_ro_paths.append(str(Path(bundle.env_ref)))

            # Try synchronous execution with a short initial wait.
            # If the executor doesn't finish in time, fall back to async dispatch.
            try:
                output = await asyncio.wait_for(
                    adaptor.executor.execute_code(
                        execution_code,
                        artifact_dir=artifact_dir,
                        trial_id=trial_id,
                        programme_id=programme_id,
                        bundle_id=bundle.id,
                        extra_ro_paths=extra_ro_paths,
                        python_exe=python_exe,
                        overlay_ro=overlay_ro,
                    ),
                    timeout=_submit_wait,
                )
                # Synchronous path — job finished within the wait window
                return _finalize_trial(store, trial_id, output)
            except asyncio.TimeoutError:
                # The job didn't finish in the wait window — dispatch async
                output = await adaptor.executor.execute_code_async(
                    trial_id, execution_code,
                    artifact_dir=artifact_dir,
                    programme_id=programme_id,
                    bundle_id=bundle.id,
                    extra_ro_paths=extra_ro_paths,
                    python_exe=python_exe,
                    overlay_ro=overlay_ro,
                )
                output_data = _parse_executor_output(output)
                if output_data.get("status") == "running":
                    # Spawn a background task to auto-finalize the trial
                    # when the executor finishes. This means the client
                    # doesn't need to poll — the trial is marked
                    # completed/failed automatically.
                    asyncio.create_task(
                        _auto_finalize(
                            trial_id, store, adaptor,
                            poll_seconds=_finalize_poll,
                        )
                    )
                    return ok({
                        "trial_id": trial_id,
                        "status": "running",
                        "message": "Trial is running in the background. "
                        "It will be auto-finalized when the executor "
                        "completes. Use get_trial_status to poll for the "
                        "result, or call record_observation once the trial "
                        "reaches 'completed'.",
                    })
                # If async dispatch returned a result immediately, finalize
                return _finalize_trial(store, trial_id, output)
        except Exception as e:
            # Only mark failed if the trial is not already terminal
            # (completed/failed/retryable are terminal — can't transition out)
            trial = store.get_trial(trial_id)
            if trial is not None and trial.status.value not in ("completed", "failed", "retryable"):
                try:
                    store.update_trial_status(trial_id, "failed")
                except Exception:
                    pass  # don't mask the original error
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def get_trial_status(programme_id: Annotated[str, Field(description='ID of the target research programme.')], trial_id: Annotated[str, Field(description='ID of the target trial.')]) -> Annotated[CallToolResult, TrialStatusOut]:
        """Check the status of a running or completed trial.

        Returns:
            {"trial_id": ..., "status": "running"|"completed"|"failed",
             "executor_output": ...}  # raw output once finalized

        executor_output is persisted on the trial row at finalize time —
        it survives server restarts (the executor's async cache does not).

        Enforcement: commitment 1 — the loop is the unit (orphan check).
        """
        try:
            trial = store.get_trial(trial_id)
            err = check_loop_is_unit(trial, programme_id)
            if err:
                return fail(json.dumps({"error": err}))

            # Check if the executor has async results
            async_output = adaptor.executor.get_async_status(trial_id)
            async_data = _parse_executor_output(async_output)

            if async_data.get("status") in ("completed", "failed", "timeout"):
                # Trial finished in background — finalize it
                return _finalize_trial(store, trial_id, async_output)

            if async_data.get("status") == "running":
                # Live telemetry: elapsed + last progress.json the
                # training code wrote + ETA (executor's own estimate or
                # pct-extrapolated — never fabricated).
                return ok({
                    "trial_id": trial_id,
                    "status": "running",
                    "started_at": trial.started_at,
                    "elapsed_seconds": async_data.get("elapsed_seconds"),
                    "progress": async_data.get("progress"),
                    "eta_seconds": async_data.get("eta_seconds"),
                    "hint": async_data.get("hint"),
                    "artifact_path": trial.artifact_path,
                    "executor_output": None,
                })

            # Unknown async status. If the trial already finalized (e.g.
            # server restarted and the executor's in-memory cache is
            # cold), serve the persisted record.
            return ok({
                "trial_id": trial_id,
                "status": trial.status.value,
                "started_at": trial.started_at,
                "finished_at": trial.finished_at,
                "duration_seconds": trial.duration_seconds,
                "artifact_path": trial.artifact_path,
                "executor_output": trial.executor_output_json,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def cancel_trial(programme_id: Annotated[str, Field(description='ID of the target research programme.')], trial_id: Annotated[str, Field(description='ID of the target trial.')]) -> Annotated[CallToolResult, CancelTrialOut]:
        """Cancel a running trial, or abandon a designed one.

        running → failed: kills the subprocess, marks failed.
        designed → abandoned: no executor to kill, no evidence lost —
        the honest terminal for a design that will never run (e.g. a
        bundle locked to code that no longer exists). The FSM has
        always permitted designed → abandoned; close_programme uses
        the same transition for programme-scoped sweeps.

        Enforcement: commitment 1 — the loop is the unit (orphan check).
        """
        try:
            trial = store.get_trial(trial_id)
            err = check_loop_is_unit(trial, programme_id)
            if err:
                return fail(json.dumps({"error": err}))

            if trial.status.value == "designed":
                store.update_trial_status(trial_id, "abandoned")
                return ok({
                    "trial_id": trial_id,
                    "status": "abandoned",
                    "cancelled": True,
                })

            if trial.status.value != "running":
                return fail(json.dumps({
                    "error": f"Trial is not running (status: {trial.status.value})",
                }))

            # Cancel the async execution
            cancel_output = await adaptor.executor.cancel_async(trial_id)
            store.update_trial_status(trial_id, "failed")
            try:
                store.update_trial_executor_output(trial_id, cancel_output)
            except Exception:
                pass
            return ok({
                "trial_id": trial_id,
                "status": "failed",
                "cancelled": True,
                "executor_output": cancel_output,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def mark_retryable(programme_id: Annotated[str, Field(description='ID of the target research programme.')], trial_id: Annotated[str, Field(description='ID of the target trial.')], reason: Annotated[str, Field(description='Why the trial is marked retryable — becomes part of the record.')]) -> Annotated[CallToolResult, MarkRetryableOut]:
        """Mark a running trial as retryable (infrastructure failure).

        This is for trials that are stuck in 'running' due to
        infrastructure issues (server restart, executor state lost,
        timeout) — NOT for scientific failures. A retryable trial is
        terminal and does not count as evidence.

        It does NOT unlock re-capture or re-run: a terminal trial's
        record is finished. To retry the work, design_experiment a new
        trial and capture_bundle on it (file-path or code://).

        Enforcement: commitment 1 — the loop is the unit (orphan check).
        """
        try:
            trial = store.get_trial(trial_id)
            err = check_loop_is_unit(trial, programme_id)
            if err:
                return fail(json.dumps({"error": err}))

            if trial.status.value != "running":
                return fail(json.dumps({
                    "error": (
                        f"Trial is not running (status: {trial.status.value}). "
                        f"Only running trials can be marked retryable."
                    ),
                }))

            store.update_trial_status(trial_id, "retryable")
            return ok({
                "trial_id": trial_id,
                "status": "retryable",
                "reason": reason,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def correct_trial_status(
        programme_id: Annotated[str, Field(description='ID of the target research programme.')], trial_id: Annotated[str, Field(description='ID of the target trial.')], to_status: Annotated[Literal['completed', 'failed', 'retryable'], Field(description='Target status: completed | failed | retryable.')], reason: Annotated[str, Field(description="Why the correction is recorded — mandatory, appended to the trial's audit trail.")]
    ) -> Annotated[CallToolResult, CorrectTrialStatusOut]:
        """Correct a terminal trial's status — the recorded repair act.

        For mislabeled records: e.g. the executor's outer wrapper
        exited 0 while the recorded output documents a crash
        ("status": "error" / nonzero inner exit code in the payload).
        Source must be completed|failed; target must be
        completed|failed|retryable. `reason` is mandatory.

        The correction is appended to the trial's executor_output_json
        under 'corrections' — the record shows both what was claimed
        and what it was corrected to. Never hand-edit the database:
        a direct sqlite3 UPDATE bypasses this audit trail.

        Enforcement: commitment 1 — the loop is the unit (orphan check).
        """
        try:
            trial = store.get_trial(trial_id)
            err = check_loop_is_unit(trial, programme_id)
            if err:
                return fail(json.dumps({"error": err}))
            if not reason or not reason.strip():
                return fail(json.dumps({
                    "error": "reason is required — a correction without "
                             "a stated basis is a silent rewrite",
                }))
            result = store.correct_trial_status(trial_id, to_status, reason)
            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_trials(programme_id: Annotated[str, Field(description='ID of the target research programme.')]) -> Annotated[CallToolResult, ListTrialsOut]:
        """List all trials in a programme with configs and status.

        Read-only enumeration — the tool-level counterpart of the
        programme://{id}/trials resource. Use it to recover a trial id
        (e.g. in a new session) rather than querying the state database
        directly.
        """
        try:
            if store.get_programme(programme_id) is None:
                return fail(json.dumps({"error": f"Programme not found: {programme_id}"}))
            trials = store.list_trials(programme_id)
            return ok({
                    "programme_id": programme_id,
                    "trials": [
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
                })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

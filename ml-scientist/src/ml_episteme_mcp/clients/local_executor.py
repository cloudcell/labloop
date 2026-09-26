"""Local executor — runs Python code in a subprocess.

This is a real executor that executes Python code on the local machine.
It captures stdout, stderr, and the exit code. No external services needed.

The executor is sandboxed by the OS process boundary. For production use
with untrusted code, wrap this in a container or VM.

When an ``artifact_dir`` is provided, the executor writes durable,
timestamped, ID-tagged artifacts to that directory instead of using a
temp file that gets deleted. This supports the artifact-workspace design
(plan-20260914-1110Z §Part 2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .roles import ExecutorRole


def _utc_timestamp_fs() -> str:
    """Return a filesystem-safe UTC timestamp (ISO 8601 with : → -)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _write_manifest(
    artifact_dir: Path,
    trial_id: str,
    programme_id: str | None,
    bundle_id: str | None,
    artifacts: list[dict],
    created_at: str,
) -> None:
    """Write the artifact manifest to the workspace directory."""
    manifest = {
        "trial_id": trial_id,
        "programme_id": programme_id,
        "bundle_id": bundle_id,
        "created_at": created_at,
        "artifacts": artifacts,
    }
    manifest_path = artifact_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


class LocalExecutor(ExecutorRole):
    """Execute Python code in a subprocess on the local machine.

    Captures stdout, stderr, and exit code. Returns structured JSON.
    Tracks execution duration for wall-time budget enforcement.

    When ``artifact_dir`` is provided to ``execute_code`` or
    ``execute_code_async``, the wrapper script, stdout, stderr, and a
    manifest are written to that directory as durable, timestamped,
    ID-tagged files. The wrapper script is NOT deleted.
    """

    def __init__(
        self,
        timeout: int = 300,
        python_exe: str | None = None,
        sandbox: str = "auto",
        trace_reads: str = "auto",
        shared_caches: list[str] | None = None,
    ):
        """Initialize the local executor.

        Args:
            timeout: Maximum execution time in seconds (default 300 = 5 min).
            python_exe: Python executable to use (default: current interpreter).
            sandbox: Isolation level for trial subprocesses —
                "auto" (bwrap full if available, else none),
                "none", "minimal" (private /tmp via bubblewrap), or
                "full" (read-only root, writes only into artifact dir).
            trace_reads: Execution-time capture of files the trial
                actually reads — "auto" (strace if available, else off),
                "on", or "off". When on, the trial command runs under
                ``strace -f`` inside the sandbox and the syscall log is
                written into the artifact dir; run_trial then captures
                the observed .py files as code snippets (executed-code
                evidence, distinct from the sealed bundle's static
                closure).
            shared_caches: Additional host directories to bind-mount rw
                at their real paths under ``full`` — beyond the built-in
                framework caches (HF_HOME, XDG_CACHE_HOME,
                TRITON_CACHE_DIR). Use for shared caches like
                ``~/.cache/pip`` or ``~/.cache/uv``. An existing dir is
                bound; a missing one is skipped and reported in
                ``executor_output.shared_caches.missing`` (no silent
                host-dir creation, no hidden absence).
        """
        import shutil

        self._timeout = timeout
        self._python_exe = python_exe or sys.executable
        self._sandbox_requested = sandbox
        self._shared_caches = [
            str(Path(p).expanduser()) for p in (shared_caches or [])
        ]
        self._has_bwrap = shutil.which("bwrap") is not None
        self._has_strace = shutil.which("strace") is not None
        if trace_reads == "auto":
            self._trace = "strace" if self._has_strace else "none"
        elif trace_reads == "on" and self._has_strace:
            self._trace = "strace"
        else:
            self._trace = "none"
        if sandbox == "auto":
            self._sandbox = "full" if self._has_bwrap else "missing"
        elif sandbox in ("minimal", "full") and not self._has_bwrap:
            # Requested isolation unavailable — the run must FAIL, not
            # silently degrade: unsealed live-file execution is exactly
            # what the seal exists to prevent. "missing" is checked at
            # execution time and returns a failed result.
            self._sandbox = "missing"
        elif sandbox in ("minimal", "full", "none"):
            self._sandbox = sandbox
        else:
            self._sandbox = "missing"
        self._cell_outputs: dict[str, str] = {}
        # Async execution state: trial_id → asyncio.Task
        self._running_tasks: dict[str, asyncio.Task] = {}
        # Completed results: trial_id → output JSON string
        self._completed_results: dict[str, str] = {}
        # Running-trial telemetry: start time + artifact dir for
        # elapsed/ETA reporting (in-memory; the DB carries durable state)
        self._started_monotonic: dict[str, float] = {}
        self._artifact_dirs: dict[str, Path] = {}

    async def execute_code(
        self,
        code: str,
        artifact_dir: Path | None = None,
        trial_id: str | None = None,
        programme_id: str | None = None,
        bundle_id: str | None = None,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        python_exe: str | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> str:
        """Execute Python code in a subprocess and return the output.

        The code is written to a file and executed with the Python
        interpreter. Stdout and stderr are captured. Duration is measured.

        When ``artifact_dir`` is provided, the wrapper script, stdout,
        stderr, and a manifest are written to that directory as durable
        artifacts. The wrapper script is NOT deleted.

        When ``artifact_dir`` is not provided, a temp file is used and
        deleted after execution (backward-compatible behavior).
        """
        ts = _utc_timestamp_fs()
        created_at = datetime.now(timezone.utc).isoformat()

        if artifact_dir is not None:
            artifact_dir = Path(artifact_dir)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            tid = trial_id or "trial-unknown"
            wrapper_name = f"{tid}_{ts}_wrapper.py"
            stdout_name = f"{tid}_{ts}_stdout.json"
            stderr_name = f"{tid}_{ts}_stderr.log"
            manifest_name = f"{tid}_{ts}_manifest.json"
            script_path = artifact_dir / wrapper_name
            with open(script_path, "w") as f:
                f.write(code)
        else:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="ml_episteme_exec_"
            ) as f:
                f.write(code)
                script_path = Path(f.name)

        # Isolation was requested (explicitly or via auto→full) but
        # bubblewrap is not installed: the run fails rather than
        # silently executing live files without the seal or sandbox.
        # sandbox="none" is the explicit opt-out.
        if self._sandbox == "missing":
            result = {
                "status": "failed",
                "error": (
                    f"bubblewrap (bwrap) not found — required for "
                    f"sandbox='{self._sandbox_requested}'; install it or "
                    "set executor.sandbox='none' to opt out"
                ),
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
                "duration_seconds": 0.0,
                "sandbox": self._sandbox_requested,
                "python_exe": python_exe or self._python_exe,
                "read_trace": self._trace,
                "seal_enforced": False,
                "sealed_overlays": 0,
            }
            if artifact_dir is not None:
                self._write_artifacts(
                    artifact_dir, trial_id, programme_id, bundle_id,
                    ts, created_at, script_path, result,
                )
            return json.dumps(result)

        try:
            start = time.monotonic()
            # Progress channel: the subprocess learns its artifact dir
            # and ids via env vars. User code may write
            # $ML_SCI_ARTIFACT_DIR/progress.json — surfaced through
            # get_async_status as live progress + ETA.
            env = dict(os.environ)
            shared_cache_report: dict | None = None
            if artifact_dir is not None:
                env["ML_SCI_ARTIFACT_DIR"] = str(artifact_dir)
                # Redirect tempfile users into the trial workspace so
                # scratch files are per-trial (and captured) instead of
                # shared. Does not help hardcoded '/tmp/...' literals —
                # the capture-time lint flags those.
                tmp_dir = artifact_dir / "tmp"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                env["TMPDIR"] = str(tmp_dir)
                if self._sandbox == "full":
                    # Framework caches must land somewhere writable.
                    # Prefer the host's existing caches — they are
                    # lock-managed shared state (HF .locks, triton
                    # cache) built for concurrent writers, and a cold
                    # per-trial cache would re-download multi-GB models
                    # on every run. Only caches with no existing host
                    # dir get a per-trial redirect.
                    shared_rw: list[str] = []
                    # (TORCHINDUCTOR is omitted: its default already
                    # lives under /tmp — the private tmpfs.)
                    for var, sub, host_default in (
                        ("XDG_CACHE_HOME", "tmp/cache", Path.home() / ".cache"),
                        ("HF_HOME", "tmp/hf", Path.home() / ".cache" / "huggingface"),
                        ("TRITON_CACHE_DIR", "tmp/triton", Path.home() / ".triton" / "cache"),
                    ):
                        host_dir = Path(env[var]) if env.get(var) else host_default
                        if host_dir.is_dir():
                            shared_rw.append(str(host_dir))
                        else:
                            p = artifact_dir / sub
                            p.mkdir(parents=True, exist_ok=True)
                            env[var] = str(p)
                    # User-declared shared caches ([executor]
                    # shared_caches): an existing dir binds rw at its
                    # real path; a missing one is skipped and reported —
                    # there is no env var to redirect, and silently
                    # creating host dirs is not this layer's business.
                    for p in self._shared_caches:
                        if Path(p).is_dir():
                            shared_rw.append(p)
                    shared_cache_report = {
                        "bound": shared_rw,
                        "missing": [
                            p for p in self._shared_caches
                            if not Path(p).is_dir()
                        ],
                    }
                    extra_rw_paths = list(extra_rw_paths or []) + shared_rw
            if trial_id:
                env["ML_SCI_TRIAL_ID"] = trial_id
            if programme_id:
                env["ML_SCI_PROGRAMME_ID"] = programme_id
            # Start the subprocess in its own process group so that
            # on timeout we can kill the entire tree (the wrapper
            # may spawn children like train_exec.py that would
            # survive a plain proc.kill()).
            # cwd: run inside the trial's artifact dir when one exists so
            # relative-path writes in user code land in a per-trial
            # workspace (captured as artifacts) instead of colliding in
            # the server process's cwd across concurrent trials.
            applied_sandbox = self._sandbox
            pyexe = python_exe or self._python_exe
            inner = [pyexe, str(script_path)]
            # Execution-time capture: strace sits INSIDE the sandbox so
            # it sees the trial's whole process tree (subprocess-spawned
            # runners included) under the same namespace paths. The log
            # lands in the artifact dir (rw in every sandbox mode) and
            # is captured as a trial artifact — the authoritative record
            # of which code files the run actually opened.
            if self._trace == "strace" and artifact_dir is not None:
                trace_log = artifact_dir / f"{trial_id or 'trial-unknown'}_{ts}_readtrace.strace"
                inner = [
                    "strace", "-f", "-qq",
                    "-e", "trace=openat,open,openat2,execve",
                    "-o", str(trace_log),
                ] + inner
            argv = self._sandbox_argv(
                inner,
                artifact_dir,
                script_path,
                extra_ro_paths,
                extra_rw_paths,
                overlay_ro,
            )
            # The seal is enforced only when a mount namespace applies
            # the overlays; without bwrap the live file runs — recorded
            # honestly in the result.
            seal_enforced = bool(overlay_ro) and applied_sandbox != "none"
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(artifact_dir) if artifact_dir is not None else None,
                start_new_session=True,  # creates a new process group
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except asyncio.TimeoutError:
                await self._kill_process_group(proc)
                duration = time.monotonic() - start
                result = {
                    "status": "timeout",
                    "error": f"Execution exceeded {self._timeout}s timeout",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                    "duration_seconds": round(duration, 3),
                    "sandbox": applied_sandbox,
                    "python_exe": pyexe,
                    "read_trace": self._trace,
                    "seal_enforced": seal_enforced,
                    "sealed_overlays": len(overlay_ro or []),
                    "shared_caches": shared_cache_report,
                }
                if artifact_dir is not None:
                    self._write_artifacts(
                        artifact_dir, trial_id, programme_id, bundle_id,
                        ts, created_at, script_path, result,
                    )
                return json.dumps(result)
            except asyncio.CancelledError:
                # Task cancelled (e.g. cancel_async). Kill the process
                # group and reap the child on this loop so the
                # subprocess transport closes cleanly.
                await self._kill_process_group(proc)
                raise

            duration = time.monotonic() - start
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Store output for read_cell_output
            cell_id = str(script_path)
            self._cell_outputs[cell_id] = stdout_str

            result = {
                "status": "completed" if proc.returncode == 0 else "failed",
                "stdout": stdout_str,
                "stderr": stderr_str,
                "exit_code": proc.returncode,
                "duration_seconds": round(duration, 3),
                "sandbox": applied_sandbox,
                "python_exe": pyexe,
                "read_trace": self._trace,
                "seal_enforced": seal_enforced,
                "sealed_overlays": len(overlay_ro or []),
                "shared_caches": shared_cache_report,
            }

            if proc.returncode != 0:
                result["error"] = f"Process exited with code {proc.returncode}"

            if artifact_dir is not None:
                self._write_artifacts(
                    artifact_dir, trial_id, programme_id, bundle_id,
                    ts, created_at, script_path, result,
                )

            return json.dumps(result)

        finally:
            if artifact_dir is None:
                script_path.unlink(missing_ok=True)

    def _sandbox_argv(
        self,
        argv: list[str],
        artifact_dir: Path | None,
        script_path: Path,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Wrap argv in a bubblewrap sandbox per self._sandbox.

        minimal — same filesystem view, but /tmp is a private tmpfs:
        hardcoded '/tmp/...' paths become per-trial instead of shared.
        full — root is read-only; only the artifact dir (and a private
        /tmp) is writable: the trial can only durably write inside its
        own workspace. Framework caches are redirected there via env.
        """
        if self._sandbox == "none" or not self._has_bwrap:
            return argv
        args = ["bwrap", "--die-with-parent"]
        if self._sandbox == "minimal":
            args += ["--dev-bind", "/", "/"]
        else:  # full — read-only root, artifact dir is the only rw mount
            args += [
                "--ro-bind", "/", "/",
                "--dev-bind", "/dev", "/dev",
                "--proc", "/proc",
            ]
        args += ["--tmpfs", "/tmp"]
        # The tmpfs over /tmp shadows the wrapper script itself whenever
        # it lives under /tmp (no-artifact-dir tempfile path) — re-bind
        # it read-only so the interpreter can still load it.
        args += ["--ro-bind", str(script_path), str(script_path)]
        # Caller-declared inputs that may live under /tmp (generator
        # scripts, code_ref files, resolved data paths) need the same
        # re-bind to survive the private tmpfs.
        seen_binds = {str(script_path)}
        for p in extra_ro_paths or []:
            p = str(p)
            if p not in seen_binds and Path(p).exists():
                seen_binds.add(p)
                args += ["--ro-bind", p, p]
        # Output locations under /tmp (e.g. a data storage dir) must be
        # re-bound writable or the write lands in the private tmpfs and
        # never reaches the host.
        for p in extra_rw_paths or []:
            p = str(p)
            if p not in seen_binds and Path(p).exists():
                seen_binds.add(p)
                args += ["--bind", p, p]
        # Seal enforcement: each sealed snippet's staged bytes are
        # bind-mounted over its original path, so the code that runs is
        # exactly what capture_bundle hashed — post-seal edits (or
        # deletions) of the live file cannot leak into the run. Applied
        # last so overlays win over the root bind and tmpfs.
        for src, target in overlay_ro or []:
            src, target = str(src), str(target)
            if Path(src).exists():
                args += ["--ro-bind", src, target]
        if artifact_dir is not None:
            # Same shadowing applies to an artifact dir under /tmp;
            # re-bind rw (required under full where root is read-only).
            args += ["--bind", str(artifact_dir), str(artifact_dir)]
            args += ["--chdir", str(artifact_dir)]
        args += ["--"] + argv
        return args

    async def _kill_process_group(self, proc) -> None:
        """Kill the child's whole process group and reap it.

        The child was started with start_new_session=True, so it is a
        group leader and SIGKILL to -pgid kills all descendants.

        After the process exits, the transport is closed and the loop
        is given one tick to run the scheduled connection_lost
        callbacks. Without this, the subprocess transport lingers and
        its __del__ raises "Event loop is closed" when GC'd later.
        """
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()
        await asyncio.sleep(0)

    def _write_artifacts(
        self,
        artifact_dir: Path,
        trial_id: str | None,
        programme_id: str | None,
        bundle_id: str | None,
        ts: str,
        created_at: str,
        script_path: Path,
        result: dict,
    ) -> None:
        """Write stdout, stderr, and manifest to the artifact directory."""
        tid = trial_id or "trial-unknown"
        stdout_name = f"{tid}_{ts}_stdout.json"
        stderr_name = f"{tid}_{ts}_stderr.log"
        manifest_name = f"{tid}_{ts}_manifest.json"

        stdout_path = artifact_dir / stdout_name
        with open(stdout_path, "w") as f:
            f.write(json.dumps(result, indent=2))

        stderr_path = artifact_dir / stderr_name
        with open(stderr_path, "w") as f:
            f.write(result.get("stderr", ""))

        artifacts = [
            {
                "id": f"art-{uuid.uuid4().hex[:8]}",
                "type": "wrapper_script",
                "filename": script_path.name,
                "sha256": _sha256_file(script_path),
                "size_bytes": script_path.stat().st_size,
            },
            {
                "id": f"art-{uuid.uuid4().hex[:8]}",
                "type": "stdout",
                "filename": stdout_name,
                "sha256": _sha256_file(stdout_path),
                "size_bytes": stdout_path.stat().st_size,
            },
            {
                "id": f"art-{uuid.uuid4().hex[:8]}",
                "type": "stderr",
                "filename": stderr_name,
                "sha256": _sha256_file(stderr_path),
                "size_bytes": stderr_path.stat().st_size,
            },
        ]
        # The strace read-trace is produced evidence — pin it too.
        # (executed_code.json is written later at finalize; the
        # manifest amendment in capture_executed_code pins it and any
        # trace files missed here.)
        for trace_path in sorted(artifact_dir.glob("*_readtrace.strace")):
            artifacts.append({
                "id": f"art-{uuid.uuid4().hex[:8]}",
                "type": "read_trace",
                "filename": trace_path.name,
                "sha256": _sha256_file(trace_path),
                "size_bytes": trace_path.stat().st_size,
            })

        manifest = {
            "trial_id": tid,
            "programme_id": programme_id,
            "bundle_id": bundle_id,
            "created_at": created_at,
            "artifacts": artifacts,
        }
        manifest_path = artifact_dir / manifest_name
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

    async def execute_code_async(
        self,
        trial_id: str,
        code: str,
        artifact_dir: Path | None = None,
        programme_id: str | None = None,
        bundle_id: str | None = None,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        python_exe: str | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> str:
        """Start execution in the background and return immediately.

        The result is stored in _completed_results when the task finishes.
        Use get_async_result(trial_id) to retrieve it.

        When ``artifact_dir`` is provided, artifacts are written to that
        directory (same as execute_code).
        """
        async def _run():
            try:
                result = await self.execute_code(
                    code,
                    artifact_dir=artifact_dir,
                    trial_id=trial_id,
                    programme_id=programme_id,
                    bundle_id=bundle_id,
                    extra_ro_paths=extra_ro_paths,
                    extra_rw_paths=extra_rw_paths,
                    python_exe=python_exe,
                    overlay_ro=overlay_ro,
                )
                self._completed_results[trial_id] = result
                return result
            except Exception as e:
                result = json.dumps({
                    "status": "failed",
                    "error": str(e),
                    "exit_code": -1,
                    "duration_seconds": 0,
                })
                self._completed_results[trial_id] = result
                return result

        task = asyncio.create_task(_run())
        self._running_tasks[trial_id] = task
        self._started_monotonic[trial_id] = time.monotonic()
        if artifact_dir is not None:
            self._artifact_dirs[trial_id] = Path(artifact_dir)
        return json.dumps({"status": "running", "trial_id": trial_id})

    def _read_progress(self, trial_id: str) -> dict | None:
        """Read the trial's progress.json if the training code wrote one."""
        artifact_dir = self._artifact_dirs.get(trial_id)
        if artifact_dir is None:
            return None
        progress_path = artifact_dir / "progress.json"
        try:
            data = json.loads(progress_path.read_text())
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _running_status(self, trial_id: str) -> str:
        """Build a live status payload for a running trial.

        Carries elapsed time plus the last progress.json the training
        code wrote (pct/message/eta_seconds). ETA is the training code's
        own estimate when provided, else extrapolated from pct; absent
        when neither exists — never fabricated.
        """
        started = self._started_monotonic.get(trial_id)
        elapsed = (
            round(time.monotonic() - started, 3) if started is not None else None
        )
        progress = self._read_progress(trial_id)
        eta = None
        if progress is not None:
            if progress.get("eta_seconds") is not None:
                eta = progress["eta_seconds"]
            else:
                pct = progress.get("pct")
                if (
                    isinstance(pct, (int, float))
                    and 0 < pct < 1
                    and elapsed is not None
                ):
                    eta = round(elapsed * (1 - pct) / pct, 3)
                elif pct is not None and pct >= 1:
                    eta = 0.0
        result = {
            "status": "running",
            "trial_id": trial_id,
            "elapsed_seconds": elapsed,
            "progress": progress,
            "eta_seconds": eta,
        }
        if progress is None:
            # ETA requires the training code to report — the harness
            # cannot know a training loop's ETA from outside. Tell the
            # client how to get it instead of silently returning null.
            result["hint"] = (
                "no progress.json — for live ETA, run_training should "
                "write $ML_SCI_ARTIFACT_DIR/progress.json as "
                "{\"pct\": 0..1, \"message\": str, \"eta_seconds\": n} "
                "at intervals"
            )
        return json.dumps(result)

    def get_async_status(self, trial_id: str) -> str:
        """Check the status of an async execution.

        Returns:
            - If still running: {"status": "running", elapsed, progress, eta}
            - If completed: the full executor output JSON
            - If unknown: {"status": "unknown"}
        """
        if trial_id in self._completed_results:
            return self._completed_results[trial_id]
        if trial_id in self._running_tasks:
            task = self._running_tasks[trial_id]
            if task.done():
                # Result should be in _completed_results, but check anyway
                if trial_id in self._completed_results:
                    return self._completed_results[trial_id]
            return self._running_status(trial_id)
        return json.dumps({"status": "unknown", "trial_id": trial_id})

    async def cancel_async(self, trial_id: str) -> str:
        """Cancel a running async execution. Kills the entire process group."""
        if trial_id not in self._running_tasks:
            return json.dumps({
                "status": "failed",
                "error": f"No running task for trial {trial_id}",
            })
        task = self._running_tasks[trial_id]
        if task.done():
            return json.dumps({
                "status": "failed",
                "error": f"Trial {trial_id} already finished",
            })
        # Cancel the asyncio task. This raises CancelledError inside
        # execute_code's proc.communicate() await. The except handler
        # below ensures the subprocess is killed even if the task
        # doesn't propagate the cancellation cleanly.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Defense-in-depth: if the subprocess is somehow still alive
        # (e.g. the task was cancelled before proc was created), find
        # and kill any process whose command line references this
        # trial's artifact dir. This catches orphaned children.
        try:
            import subprocess
            result = subprocess.run(
                ["pgrep", "-f", trial_id],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for pid_str in result.stdout.strip().split("\n"):
                    pid_str = pid_str.strip()
                    if pid_str and pid_str != str(os.getpid()):
                        try:
                            pgid = os.getpgid(int(pid_str))
                            os.killpg(pgid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, ValueError):
                            try:
                                os.kill(int(pid_str), signal.SIGKILL)
                            except (ProcessLookupError, PermissionError):
                                pass
        except Exception:
            pass  # best-effort cleanup
        del self._running_tasks[trial_id]
        return json.dumps({"status": "cancelled", "trial_id": trial_id})

    def clear_async(self, trial_id: str) -> None:
        """Clean up async state for a trial."""
        self._running_tasks.pop(trial_id, None)
        self._completed_results.pop(trial_id, None)

    async def read_cell_output(self, cell_id: str) -> str:
        """Read the output from a previously executed cell (script path)."""
        output = self._cell_outputs.get(cell_id, "")
        return json.dumps({"cell_id": cell_id, "output": output})

#!/usr/bin/env python
"""End-to-end demo against a live HTTP MCP server.

Tests the full scientific loop with data provenance, artifacts, and
the session protocol — over the real HTTP transport.

Usage:
    # Terminal 1: start the server
    uv run ml-episteme-mcp --transport http --port 38080 --stateless --observability-port 38081

    # Terminal 2: run this demo
    uv run python scripts/e2e_live.py

The demo uses your real state DB (~/.ml-episteme/state.db), so you can
inspect the results in the observability GUI at http://localhost:38081.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

SERVER_URL = "http://localhost:38080/mcp"

# Fixture scripts (relative to project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_SCRIPT = str(PROJECT_ROOT / "tests" / "fixtures" / "generate_stub.py")
TRAINING_SCRIPT = str(PROJECT_ROOT / "tests" / "fixtures" / "train_with_data.py")


def show(step: int, title: str, result: dict | None = None) -> None:
    print(f"\n{'='*60}")
    print(f"  Step {step}: {title}")
    print(f"{'='*60}")
    if result is not None:
        if "error" in result:
            print(f"  ❌ ERROR: {result['error']}")
        else:
            for k, v in result.items():
                if k == "executor_output":
                    print(f"  {k}: (see artifacts)")
                elif isinstance(v, str) and len(v) > 80:
                    print(f"  {k}: {v[:77]}...")
                elif isinstance(v, dict):
                    print(f"  {k}: {json.dumps(v, indent=2)}")
                elif isinstance(v, list):
                    print(f"  {k}: {v}")
                else:
                    print(f"  {k}: {v}")


async def call(session, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text)


async def read(session, uri: str) -> dict:
    result = await session.read_resource(uri)
    return json.loads(result.contents[0].text)


async def main() -> None:
    print("\n" + "█" * 60)
    print("  ML-Scientist E2E Live Demo — Data Provenance Loop")
    print("█" * 60)
    print(f"  Server: {SERVER_URL}")
    print(f"  Generator: {GENERATOR_SCRIPT}")
    print(f"  Training:  {TRAINING_SCRIPT}")

    async with streamable_http_client(SERVER_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # --- Step 0: Read the session protocol ---
            print("\n" + "─" * 60)
            print("  Step 0: Read protocol://session")
            print("─" * 60)
            protocol = await read(session, "protocol://session")
            print(f"  Loop steps: {len(protocol['loop_steps'])}")
            print(f"  Tools: {len(protocol['tool_catalog'])}")
            print(f"  Resources: {len(protocol['resources'])}")
            print(f"  Active programmes: {len(protocol['current_state']['active_programmes'])}")
            for step in protocol["loop_steps"]:
                print(f"    {step['step']:2d}. {step['name']}")

            # --- Step 1: create_programme ---
            prog = await call(session, "create_programme", {
                "goal": "E2E demo: test whether learning rate affects validation accuracy",
                "constraints": {"gpu_memory_gb": 8.0},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
                "metric_direction": "maximize",
            })
            pid = prog["programme_id"]
            show(1, "create_programme", prog)

            # --- Step 2: formulate_hypothesis ---
            hyp = await call(session, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "lr=0.01 produces higher val_accuracy than lr=0.001",
                "failure_criterion": "val_accuracy(lr=0.01) <= val_accuracy(lr=0.001)",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            show(2, "formulate_hypothesis", hyp)

            # --- Step 3a: prepare_data (train, generated) ---
            train_ref = await call(session, "prepare_data", {
                "split": "train",
                "regime": "generated",
                "generator_code_ref": GENERATOR_SCRIPT,
                "generator_seed": 42,
                "generator_params": {"n_samples": 200},
            })
            train_ref_id = train_ref["data_ref_id"]
            show(3, "prepare_data (train)", train_ref)

            # --- Step 3b: prepare_data (validation, generated) ---
            val_ref = await call(session, "prepare_data", {
                "split": "validation",
                "regime": "generated",
                "generator_code_ref": GENERATOR_SCRIPT,
                "generator_seed": 43,
                "generator_params": {"n_samples": 50},
            })
            val_ref_id = val_ref["data_ref_id"]
            show(3, "prepare_data (validation)", val_ref)

            # --- Step 3c: verify_data ---
            verify = await call(session, "verify_data", {
                "data_ref_id": train_ref_id,
            })
            show(3, "verify_data (train)", verify)

            # --- Step 3d: Read the dataref resource ---
            dataref = await read(session, f"dataref://{train_ref_id}")
            print(f"\n  dataref://{train_ref_id}:")
            print(f"    regime: {dataref['regime']}")
            print(f"    split: {dataref['split']}")
            print(f"    content_hash: {dataref['content_hash']}")
            print(f"    storage_uri: {dataref['storage_uri']}")
            print(f"    reproducibility_risk: {dataref['reproducibility_risk']}")

            # --- Step 4: design_experiment ---
            trial = await call(session, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"lr": 0.01, "seeds": [42, 43]},
            })
            tid = trial["trial_id"]
            show(4, "design_experiment", trial)

            # --- Step 5: capture_bundle (with data_refs) ---
            bundle = await call(session, "capture_bundle", {
                "trial_id": tid,
                "code_ref": TRAINING_SCRIPT,
                "env_ref": "python:3.12",
                "seeds": [42, 43],
                "splits": {"train": 0.8, "validation": 0.2},
                "data_refs": [train_ref_id, val_ref_id],
            })
            show(5, "capture_bundle (with data_refs)", bundle)

            # --- Step 6: run_trial ---
            run = await call(session, "run_trial", {
                "programme_id": pid,
                "trial_id": tid,
            })
            show(6, "run_trial", run)

            # If still running, poll
            if run.get("status") == "running":
                print("  (polling for completion...)")
                for _ in range(10):
                    await asyncio.sleep(2)
                    status = await call(session, "get_trial_status", {
                        "programme_id": pid,
                        "trial_id": tid,
                    })
                    if status.get("status") in ("completed", "failed"):
                        run = status
                        break
                show(6, "run_trial (finalized)", run)

            # --- Step 6b: Read trial artifacts ---
            if run.get("status") == "completed":
                artifacts = await read(session, f"trial://{tid}/artifacts")
                print(f"\n  trial://{tid}/artifacts:")
                print(f"    artifact_path: {artifacts.get('artifact_path', 'N/A')}")
                manifest = artifacts.get("manifest", {})
                print(f"    trial_id: {manifest.get('trial_id', 'N/A')}")
                print(f"    bundle_id: {manifest.get('bundle_id', 'N/A')}")
                for art in manifest.get("artifacts", []):
                    print(f"    artifact: {art['type']} -> {art['filename']} ({art['sha256'][:20]}...)")

                # Verify the artifact files exist on disk
                artifact_path = Path(artifacts.get("artifact_path", ""))
                if artifact_path.exists():
                    files = list(artifact_path.iterdir())
                    print(f"    Files on disk: {len(files)}")
                    for f in sorted(files):
                        print(f"      {f.name} ({f.stat().st_size} bytes)")

            # --- Step 7: record_observation ---
            obs = await call(session, "record_observation", {
                "programme_id": pid,
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.85},
                "variance": {"val_accuracy": 0.01},
                "spatiotemporal_region": "local/2026-09-14T12:00:00Z",
            })
            show(7, "record_observation", obs)

            # --- Step 8: update_belief ---
            belief = await call(session, "update_belief", {
                "programme_id": pid,
                "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            show(8, "update_belief", belief)

            # --- Step 9: conclude_hypothesis ---
            conclusion = await call(session, "conclude_hypothesis", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "verdict": "accepted",
                "evidence_summary": "lr=0.01 produced val_accuracy=0.85 > lr=0.001 baseline",
            })
            show(9, "conclude_hypothesis", conclusion)

            # --- Step 10: close_programme ---
            close = await call(session, "close_programme", {
                "programme_id": pid,
            })
            show(10, "close_programme", close)

            # --- Final: Read the session protocol again ---
            print("\n" + "─" * 60)
            print("  Final: Read protocol://session (dynamic state)")
            print("─" * 60)
            protocol = await read(session, "protocol://session")
            progs = protocol["current_state"]["active_programmes"]
            print(f"  Active programmes: {len(progs)}")
            print(f"  (our programme {pid} should be completed)")

            # --- Verify the data files are read-only ---
            print("\n" + "─" * 60)
            print("  Verification: Data files are read-only")
            print("─" * 60)
            if dataref.get("storage_uri"):
                data_path = Path(dataref["storage_uri"][7:])  # strip file://
                if data_path.exists():
                    mode = data_path.stat().st_mode
                    is_ro = not (mode & stat.S_IWUSR)
                    print(f"  {data_path.name}: {'read-only ✓' if is_ro else 'WRITABLE ✗'}")
                    print(f"    mode: {oct(mode & 0o777)}")
                    print(f"    path: {data_path}")

    print("\n" + "█" * 60)
    print("  E2E Live Demo Complete!")
    print("█" * 60)
    print(f"  Programme: {pid}")
    print(f"  Check the observability GUI: http://localhost:38081")
    print()


if __name__ == "__main__":
    asyncio.run(main())

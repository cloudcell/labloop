#!/usr/bin/env python
"""End-to-end demo: the full scientific loop with data provenance.

This script exercises every new feature:
  - prepare_data (generated regime, runs generator, computes hash)
  - verify_data (re-computes hash, checks integrity)
  - design_experiment + capture_bundle with data_refs
  - run_trial (resolves DataRefs to read-only paths, injects into config)
  - record_observation + update_belief
  - conclude_hypothesis + close_programme
  - trial://{id}/artifacts resource (artifact manifest)
  - dataref://{id} resource (data provenance)
  - protocol://session resource (loop steps, tool catalog, dynamic state)

Usage:
    uv run python scripts/e2e_demo.py

The script uses a temporary database and data directory, so it doesn't
touch your real state. It prints each step so you can follow the loop.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from mcp.client import Client

from ml_episteme_mcp.clients.adaptor import create_local_adaptor
from ml_episteme_mcp.server import create_server
from ml_episteme_mcp.state.store import StateStore


# --- Paths to fixture scripts ---

GENERATOR_SCRIPT = str(Path(__file__).parent.parent / "tests" / "fixtures" / "generate_stub.py")
TRAINING_SCRIPT = str(Path(__file__).parent.parent / "tests" / "fixtures" / "train_with_data.py")


def print_step(step: int, title: str, result: dict | None = None) -> None:
    print(f"\n{'='*60}")
    print(f"  Step {step}: {title}")
    print(f"{'='*60}")
    if result is not None:
        if "error" in result:
            print(f"  ❌ ERROR: {result['error']}")
        else:
            # Print key fields, not the full JSON
            for k, v in result.items():
                if k == "executor_output":
                    print(f"  {k}: (see artifacts)")
                elif isinstance(v, str) and len(v) > 80:
                    print(f"  {k}: {v[:77]}...")
                elif isinstance(v, dict):
                    print(f"  {k}: {json.dumps(v, indent=2)}")
                else:
                    print(f"  {k}: {v}")


async def read_resource(client, uri: str) -> dict:
    result = await client.read_resource(uri)
    return json.loads(result.contents[0].text)


async def call_tool(client, name: str, args: dict) -> dict:
    result = await client.call_tool(name, args)
    text = result.content[0].text
    return json.loads(text)


async def main() -> None:
    # Use a temporary directory so we don't touch real state
    with tempfile.TemporaryDirectory(prefix="ml-episteme-e2e-") as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "state.db"
        data_dir = tmpdir / "data"

        store = StateStore(db_path)
        store.connect()

        # Create a local adaptor with real executor + data handler
        adaptor = create_local_adaptor(
            memory_db_path=str(tmpdir / "memory.db"),
            data_dir=str(data_dir),
        )

        server = create_server(store, adaptor)
        client = Client(server)

        # Phase 3: enable archiving for the demo
        from ml_episteme_mcp.archive import ArchiveConfig, Archiver
        archive_config = ArchiveConfig(
            enabled=True,
            batch_size=10,
            archive_dir=str(tmpdir / "archives"),
        )
        archiver = Archiver(store, archive_config)
        # Recreate the server with the archiver wired in
        server = create_server(store, adaptor, archiver=archiver)
        client = Client(server)

        print("\n" + "█" * 60)
        print("  ML-Scientist E2E Demo — Full Loop with Data Provenance")
        print("█" * 60)
        print(f"  Temp dir: {tmpdir}")
        print(f"  DB: {db_path}")
        print(f"  Data dir: {data_dir}")

        async with client:
            # --- Step 0: Read the session protocol ---
            print("\n" + "─" * 60)
            print("  Step 0: Read protocol://session")
            print("─" * 60)
            protocol = await read_resource(client, "protocol://session")
            print(f"  Loop steps: {len(protocol['loop_steps'])}")
            print(f"  Tools: {len(protocol['tool_catalog'])}")
            print(f"  Resources: {len(protocol['resources'])}")
            print(f"  Active programmes: {len(protocol['current_state']['active_programmes'])}")
            for step in protocol["loop_steps"]:
                print(f"    {step['step']:2d}. {step['name']}")

            # --- Step 1: create_programme ---
            prog = await call_tool(client, "create_programme", {
                "goal": "Test whether learning rate affects validation accuracy",
                "constraints": {"gpu_memory_gb": 8.0},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
                "metric_direction": "maximize",
            })
            pid = prog["programme_id"]
            print_step(1, "create_programme", prog)

            # --- Step 2: formulate_hypothesis ---
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "lr=0.01 produces higher val_accuracy than lr=0.001",
                "failure_criterion": "val_accuracy(lr=0.01) <= val_accuracy(lr=0.001)",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            print_step(2, "formulate_hypothesis", hyp)

            # --- Step 3a: prepare_data (train, generated) ---
            train_ref = await call_tool(client, "prepare_data", {
                "split": "train",
                "regime": "generated",
                "generator_code_ref": GENERATOR_SCRIPT,
                "generator_seed": 42,
                "generator_params": {"n_samples": 200},
            })
            train_ref_id = train_ref["data_ref_id"]
            print_step(3, "prepare_data (train)", train_ref)

            # --- Step 3b: prepare_data (validation, generated) ---
            val_ref = await call_tool(client, "prepare_data", {
                "split": "validation",
                "regime": "generated",
                "generator_code_ref": GENERATOR_SCRIPT,
                "generator_seed": 43,  # different seed = different data
                "generator_params": {"n_samples": 50},
            })
            val_ref_id = val_ref["data_ref_id"]
            print_step(3, "prepare_data (validation)", val_ref)

            # --- Step 3c: verify_data ---
            verify = await call_tool(client, "verify_data", {
                "data_ref_id": train_ref_id,
            })
            print_step(3, "verify_data (train)", verify)

            # --- Step 3d: Read the dataref resource ---
            dataref = await read_resource(client, f"dataref://{train_ref_id}")
            print(f"\n  dataref://{train_ref_id}:")
            print(f"    regime: {dataref['regime']}")
            print(f"    split: {dataref['split']}")
            print(f"    content_hash: {dataref['content_hash']}")
            print(f"    storage_uri: {dataref['storage_uri']}")
            print(f"    reproducibility_risk: {dataref['reproducibility_risk']}")

            # --- Step 4: design_experiment ---
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"lr": 0.01, "seeds": [42, 43]},
            })
            tid = trial["trial_id"]
            print_step(4, "design_experiment", trial)

            # --- Step 5: capture_bundle (with data_refs) ---
            bundle = await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": TRAINING_SCRIPT,
                "env_ref": "python:3.12",
                "seeds": [42, 43],
                "splits": {"train": 0.8, "validation": 0.2},
                "data_refs": [train_ref_id, val_ref_id],
            })
            print_step(5, "capture_bundle (with data_refs)", bundle)

            # --- Step 6: run_trial ---
            run = await call_tool(client, "run_trial", {
                "programme_id": pid,
                "trial_id": tid,
            })
            print_step(6, "run_trial", run)

            # If still running, poll
            if run.get("status") == "running":
                print("  (polling for completion...)")
                for _ in range(10):
                    await asyncio.sleep(2)
                    status = await call_tool(client, "get_trial_status", {
                        "programme_id": pid,
                        "trial_id": tid,
                    })
                    if status.get("status") in ("completed", "failed"):
                        run = status
                        break
                print_step(6, "run_trial (finalized)", run)

            # --- Step 6b: Read trial artifacts ---
            if run.get("status") == "completed":
                artifacts = await read_resource(client, f"trial://{tid}/artifacts")
                print(f"\n  trial://{tid}/artifacts:")
                print(f"    artifact_path: {artifacts.get('artifact_path', 'N/A')}")
                manifest = artifacts.get("manifest", {})
                print(f"    trial_id: {manifest.get('trial_id', 'N/A')}")
                print(f"    bundle_id: {manifest.get('bundle_id', 'N/A')}")
                for art in manifest.get("artifacts", []):
                    print(f"    artifact: {art['type']} → {art['filename']} ({art['sha256'][:20]}...)")

                # Verify the artifact files exist on disk
                artifact_path = Path(artifacts.get("artifact_path", ""))
                if artifact_path.exists():
                    files = list(artifact_path.iterdir())
                    print(f"    Files on disk: {len(files)}")
                    for f in sorted(files):
                        print(f"      {f.name} ({f.stat().st_size} bytes)")

            # --- Step 7: record_observation ---
            obs = await call_tool(client, "record_observation", {
                "programme_id": pid,
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.85},
                "variance": {"val_accuracy": 0.01},
                "spatiotemporal_region": "local/2026-09-14T12:00:00Z",
            })
            print_step(7, "record_observation", obs)

            # --- Step 8: update_belief ---
            belief = await call_tool(client, "update_belief", {
                "programme_id": pid,
                "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            print_step(8, "update_belief", belief)

            # --- Step 9: conclude_hypothesis ---
            conclusion = await call_tool(client, "conclude_hypothesis", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "verdict": "accepted",
                "evidence_summary": "lr=0.01 produced val_accuracy=0.85 > lr=0.001 baseline",
            })
            print_step(9, "conclude_hypothesis", conclusion)

            # --- Step 10: close_programme ---
            close = await call_tool(client, "close_programme", {
                "programme_id": pid,
            })
            print_step(10, "close_programme", close)

            # --- Final: Read the session protocol again ---
            print("\n" + "─" * 60)
            print("  Final: Read protocol://session (dynamic state)")
            print("─" * 60)
            protocol = await read_resource(client, "protocol://session")
            progs = protocol["current_state"]["active_programmes"]
            print(f"  Active programmes: {len(progs)}")
            # Our programme should be completed (not in active list)
            print(f"  (our programme {pid} should be completed)")

            # Verify the data files are read-only
            print("\n" + "─" * 60)
            print("  Verification: Data files are read-only")
            print("─" * 60)
            train_data_ref = store.get_data_ref(train_ref_id)
            if train_data_ref and train_data_ref.storage_uri:
                data_path = Path(train_data_ref.storage_uri[7:])
                import os, stat
                mode = data_path.stat().st_mode
                is_readonly = not (mode & stat.S_IWUSR)
                print(f"  {data_path.name}: {'read-only ✓' if is_readonly else 'WRITABLE ✗'}")
                print(f"    mode: {oct(mode & 0o777)}")

        store.close()

    print("\n" + "█" * 60)
    print("  E2E Demo Complete!")
    print("█" * 60)
    print(f"  All artifacts and data were in: {tmpdir}")
    print("  (cleaned up automatically)")
    print()


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
SOL-007 daily health snapshot writer.
Run update_health.py + fetch_yield.py, build the canonical snapshot JSON,
write it to the workspace, and print one structured status line.

Exit codes:
  0 — ok / alert (no action required)
  1 — breach (agent must alert + log)
  2 — pipeline error (agent reports, no tripwire action)
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DASH = Path("/Users/clayton/sol007-dashboard")
WORKSPACE = Path("/Users/clayton/.openclaw/workspace")
SNAPSHOT_DIR = WORKSPACE / "memory/solana-trading/pilot-health"
BASELINE_PATH = SNAPSHOT_DIR / "baseline-2026-05-03.json"
INCIDENTS_LOG = WORKSPACE / "memory/solana-trading/pilot-incidents.log"


def run_script(name: str) -> bool:
    """Run a dashboard script, suppressing output. Return True on success."""
    result = subprocess.run(
        [sys.executable, str(DASH / "scripts" / name)],
        cwd=str(DASH),
        capture_output=True,
    )
    return result.returncode == 0


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as e:
        return None


def atomic_write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    # --- Step 1: run scripts ---
    if not run_script("update_health.py"):
        print("PIPELINE_ERROR: update_health.py failed")
        sys.exit(2)
    if not run_script("fetch_yield.py"):
        print("PIPELINE_ERROR: fetch_yield.py failed")
        sys.exit(2)

    # --- Step 2: read outputs ---
    latest = load_json(DASH / "data/latest.json")
    returns = load_json(DASH / "data/returns.json")

    if latest is None:
        print("PIPELINE_ERROR: latest.json missing or unreadable")
        sys.exit(2)

    # --- Step 3: extract fields ---
    legs = latest.get("legs", {})
    jitosol = legs.get("jitosol", legs.get("JitoSOL", {}))
    inf = legs.get("inf", legs.get("INF", {}))
    tripwires = latest.get("tripwires", {})

    r2 = tripwires.get("R2", {}).get("status", "unknown")
    r5 = tripwires.get("R5", {}).get("status", "unknown")
    r7 = tripwires.get("R7", {}).get("status", "unknown")
    drift_pct = latest.get("drift_pct", None)
    total_sol = latest.get("total_sol_equivalent", None)
    tripwire_status = latest.get("tripwire_status", "unknown")
    price_fetch_ok = latest.get("price_fetch_ok", True)
    # price_fetch_ok may be absent; infer from R8 or default True
    if "R8" in tripwires:
        price_fetch_ok = tripwires["R8"].get("status", "ok") != "error"

    apy_pct = None
    yield_sol_total = None
    if returns:
        apy_pct = returns.get("annualized_apy", returns.get("apy_pct", None))
        yield_sol_total = returns.get("yield_sol_total", None)

    baseline_sol = 25.0  # deployment amount; also in baseline JSON
    baseline = load_json(BASELINE_PATH)
    if baseline:
        baseline_sol = baseline.get("deposit_baseline_sol",
                       baseline.get("total_sol_equivalent_at_deposit", 25.0))

    # --- Step 4: build snapshot ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot_path = SNAPSHOT_DIR / f"{today}.json"

    snapshot = {
        "date": today,
        "timestamp": latest.get("updated_at", datetime.now(timezone.utc).isoformat()),
        "pilot_pubkey": latest.get("pilot_pubkey", ""),
        "jitosol_balance": jitosol.get("balance"),
        "jitosol_sol_price": jitosol.get("sol_price"),
        "inf_balance": inf.get("balance"),
        "inf_sol_price": inf.get("sol_price"),
        "native_sol": latest.get("native_sol", 0.0),
        "total_sol_equivalent": total_sol,
        "baseline_sol": baseline_sol,
        "drift_pct": drift_pct,
        "yield_sol_total": yield_sol_total,
        "apy_pct": apy_pct,
        "tripwire_status": tripwire_status,
        "r2_status": r2,
        "r5_status": r5,
        "r7_status": r7,
        "price_fetch_ok": price_fetch_ok,
        "r2_note": tripwires.get("R2", {}).get("note", ""),
        "r5_note": tripwires.get("R5", {}).get("note", ""),
    }

    atomic_write(snapshot_path, snapshot)
    size = snapshot_path.stat().st_size

    # --- Step 5: format status and evaluate breaches ---
    apy_str = f"{apy_pct:.2f}" if apy_pct is not None else "?"
    drift_str = f"{drift_pct:.4f}" if drift_pct is not None else "?"

    breach_flags = []
    if not price_fetch_ok:
        print(
            f"STATUS=data_fetch_error snapshot={snapshot_path.name} "
            f"size={size}B drift={drift_str}% r2={r2} r5=skip(no_price) r7={r7} apy={apy_str}%"
        )
        sys.exit(0)

    if r5 == "breached":
        breach_flags.append("R5")
    if r7 == "breached":
        breach_flags.append("R7")
    if r2 == "breached":
        breach_flags.append("R2")

    if breach_flags:
        print(
            f"STATUS=BREACH({','.join(breach_flags)}) snapshot={snapshot_path.name} "
            f"size={size}B drift={drift_str}% r2={r2} r5={r5} r7={r7} apy={apy_str}%"
        )
        sys.exit(1)

    print(
        f"STATUS=ok snapshot={snapshot_path.name} size={size}B "
        f"drift={drift_str}% r2={r2} r5={r5} r7={r7} apy={apy_str}%"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()

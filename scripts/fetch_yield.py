#!/usr/bin/env python3
"""Fetch SOL-007 LST exchange rates and update dashboard yield-return files."""
import json
import os
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BASELINE_PATH = DATA / "baseline.json"
RETURNS_PATH = DATA / "returns.json"
HISTORY_PATH = DATA / "returns_history.jsonl"
SOL_MINT = "So11111111111111111111111111111111111111112"
DEPLOYED_AT = datetime(2026, 5, 3, 18, 5, 0, tzinfo=timezone.utc)
FIXED_AMOUNTS = {
    "jitosol": 15.661410365,
    "inf": 3.518819517,
}


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "sol007-yield-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def jupiter_sol_price(mint):
    # Jupiter price v3 returns USD prices. Convert LST/USD ÷ SOL/USD into LST/SOL.
    url = f"https://lite-api.jup.ag/price/v3?ids={mint},{SOL_MINT}"
    data = get_json(url)
    token_usd = float(data[mint]["usdPrice"])
    sol_usd = float(data[SOL_MINT]["usdPrice"])
    if sol_usd <= 0:
        raise ValueError("Jupiter returned non-positive SOL USD price")
    return token_usd / sol_usd, {"provider": "jupiter-price-v3", "url": url, "token_usd": token_usd, "sol_usd": sol_usd}


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
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
    baseline = json.loads(BASELINE_PATH.read_text())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    days_elapsed = max((now - DEPLOYED_AT).total_seconds() / 86400.0, 1e-9)

    legs = {}
    total_yield = 0.0
    sources = {}
    for key in ("jitosol", "inf"):
        base_leg = baseline["legs"][key]
        current_price, source = jupiter_sol_price(base_leg["mint"])
        amount = FIXED_AMOUNTS[key]
        baseline_price = float(base_leg["price_sol_per_token"])
        yield_sol = (current_price - baseline_price) * amount
        total_yield += yield_sol
        legs[key] = {
            "label": base_leg.get("label", key),
            "mint": base_leg["mint"],
            "amount_token": amount,
            "baseline_price_sol_per_token": baseline_price,
            "current_price_sol_per_token": current_price,
            "yield_sol": yield_sol,
        }
        sources[key] = source

    yield_pct_total = total_yield / 25.0 * 100.0
    snapshot = {
        "snapshot_at": now.isoformat().replace("+00:00", "Z"),
        "deployed_at": DEPLOYED_AT.isoformat().replace("+00:00", "Z"),
        "days_elapsed": days_elapsed,
        "baseline_sol": 25.0,
        "yield_sol_jitosol": legs["jitosol"]["yield_sol"],
        "yield_sol_inf": legs["inf"]["yield_sol"],
        "yield_sol_total": total_yield,
        "yield_pct_total": yield_pct_total,
        "annualized_apy": (yield_pct_total / days_elapsed) * 365.0,
        "legs": legs,
        "sources": sources,
    }

    atomic_write_json(RETURNS_PATH, snapshot)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps(snapshot, indent=2, sort_keys=True))

    # Push updated returns.json to GitHub Pages so the public dashboard stays current.
    # Only commit data/returns.json (not history, to keep diff small).
    try:
        apy = snapshot["annualized_apy"]
        ts = snapshot["snapshot_at"]
        commit_msg = f"yield update {ts} APY={apy:.2f}%"
        subprocess.run(["git", "add", "data/returns.json", "data/returns_history.jsonl"], cwd=str(ROOT), check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(ROOT), capture_output=True
        )
        if result.returncode != 0:  # staged changes exist
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(ROOT), check=True, capture_output=True)
            subprocess.run(["git", "push"], cwd=str(ROOT), check=True, capture_output=True)
            print(f"[git] pushed: {commit_msg}")
        else:
            print("[git] no change to commit")
    except subprocess.CalledProcessError as e:
        print(f"[git] push failed: {e.stderr.decode().strip() if e.stderr else e}")


if __name__ == "__main__":
    main()

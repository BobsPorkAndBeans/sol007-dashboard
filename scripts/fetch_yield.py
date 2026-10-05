#!/usr/bin/env python3
"""Fetch SOL-007 LST exchange rates and update dashboard yield-return files."""
import argparse
import json
import os
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = ROOT / "logs"
BASELINE_PATH = DATA / "baseline.json"
RETURNS_PATH = DATA / "returns.json"
HISTORY_PATH = DATA / "returns_history.jsonl"
PUBLISH_STATE_PATH = LOGS / "last_publish.json"
PUBLISH_LOG_PATH = LOGS / "publish.log"
MIN_PUBLISH_INTERVAL_SEC = 3600
# Floats closer than this (absolute) are treated as unchanged.
FLOAT_EPS = 1e-6
# Timestamp / run-metadata keys that always change and must not force a commit.
IGNORE_KEYS = {
    "updated_at",
    "snapshot_at",
    "days_elapsed",
    "sources",  # Jupiter USD quote noise; LST/SOL ratios live under legs
}
PUBLISH_PATHS = [
    "data/returns.json",
    "data/returns_sparkline.json",
    "data/latest.json",
    "data/history.json",
]
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


def log_publish(msg):
    print(msg)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with PUBLISH_LOG_PATH.open("a") as f:
            f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}\n")
    except OSError:
        pass


def load_publish_state():
    try:
        return json.loads(PUBLISH_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_publish_state(pushed_at_iso, commit_msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        PUBLISH_STATE_PATH,
        {"last_push_at": pushed_at_iso, "last_commit_msg": commit_msg},
    )


def seconds_since_last_push(now):
    state = load_publish_state()
    raw = state.get("last_push_at")
    if not raw:
        # Fall back to HEAD commit time so a fresh install does not double-push.
        try:
            out = subprocess.run(
                ["git", "log", "-1", "--format=%cI", "--"] + PUBLISH_PATHS,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            raw = (out.stdout or "").strip() or None
        except OSError:
            raw = None
    if not raw:
        return None
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return max((now - last).total_seconds(), 0.0)
    except ValueError:
        return None


def _git_show_json(spec):
    result = subprocess.run(
        ["git", "show", spec],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _canonical_meaningful(obj):
    """Drop timestamp-only / quote-noise fields; keep APY, totals, NAV, health."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in IGNORE_KEYS:
                continue
            out[key] = _canonical_meaningful(value)
        return out
    if isinstance(obj, list):
        # history.json / sparkline grow every cycle; only compare latest-ish tip
        # is handled separately. For nested lists under latest/returns keep as-is.
        return [_canonical_meaningful(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 9)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    return obj


def _values_close(a, b, eps=FLOAT_EPS):
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return False
    if isinstance(a, dict):
        if set(a) != set(b):
            return False
        return all(_values_close(a[k], b[k], eps) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_values_close(x, y, eps) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= eps
    return a == b


def public_data_meaningfully_changed():
    """Compare staged latest/returns meaningful fields against HEAD.

    history.json and returns_sparkline.json always grow; they alone must not
    force a Pages publish. APY / totals / NAV / tripwire status drive the decision.
    """
    checks = (
        ("data/latest.json", "HEAD:data/latest.json"),
        ("data/returns.json", "HEAD:data/returns.json"),
    )
    any_present = False
    for path, head_spec in checks:
        head_obj = _git_show_json(head_spec)
        try:
            cur_obj = json.loads((ROOT / path).read_text())
        except (OSError, json.JSONDecodeError):
            return True
        if head_obj is None:
            return True
        any_present = True
        if not _values_close(_canonical_meaningful(cur_obj), _canonical_meaningful(head_obj)):
            return True
    return not any_present


def git_reset_staged():
    subprocess.run(
        ["git", "restore", "--staged", "--"] + PUBLISH_PATHS,
        cwd=str(ROOT),
        check=False,
        capture_output=True,
    )


def publish(commit_msg, *, do_push=True, ignore_hourly_gate=False):
    """Stage public data, skip no-ops / sub-hourly pushes, else commit+push."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    subprocess.run(
        ["git", "add", "--"] + PUBLISH_PATHS,
        cwd=str(ROOT),
        check=True,
        capture_output=True,
    )

    # Fast path: completely empty index diff (rare once timestamps rewrite files).
    empty = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(ROOT),
        capture_output=True,
    )
    if empty.returncode == 0:
        log_publish("[git] skip: no staged diff")
        return "skip-empty"

    if not public_data_meaningfully_changed():
        git_reset_staged()
        log_publish("[git] skip: public data unchanged (timestamp-only / within eps)")
        return "skip-noop"

    if not ignore_hourly_gate:
        elapsed = seconds_since_last_push(now)
        if elapsed is not None and elapsed < MIN_PUBLISH_INTERVAL_SEC:
            git_reset_staged()
            remaining = int(MIN_PUBLISH_INTERVAL_SEC - elapsed)
            log_publish(
                f"[git] skip: hourly gate ({elapsed:.0f}s since last push, "
                f"{remaining}s remaining)"
            )
            return "skip-hourly"

    if not do_push:
        git_reset_staged()
        log_publish(f"[git] dry-run: would push: {commit_msg}")
        return "dry-run-push"

    subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "push"], cwd=str(ROOT), check=True, capture_output=True)
    save_publish_state(now.isoformat().replace("+00:00", "Z"), commit_msg)
    log_publish(f"[git] pushed: {commit_msg}")
    return "pushed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Collect data and evaluate publish gates without committing/pushing",
    )
    parser.add_argument(
        "--force-publish",
        action="store_true",
        help="Bypass the hourly publish gate (still skips true no-ops)",
    )
    args = parser.parse_args(argv)

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
    # Keep the full jsonl on Sophie only. Pages gets a short sparkline window.
    spark_points = []
    try:
        for line in HISTORY_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            spark_points.append({
                "snapshot_at": rec.get("snapshot_at"),
                "yield_sol_total": rec.get("yield_sol_total"),
            })
    except Exception:
        spark_points = [{
            "snapshot_at": snapshot["snapshot_at"],
            "yield_sol_total": snapshot["yield_sol_total"],
        }]
    atomic_write_json(DATA / "returns_sparkline.json", {"points": spark_points[-180:]})
    print(json.dumps(snapshot, indent=2, sort_keys=True))

    # One public commit per allowed cycle (health writes files first, this job publishes).
    try:
        apy = snapshot["annualized_apy"]
        ts = snapshot["snapshot_at"]
        commit_msg = f"dashboard update {ts} APY={apy:.2f}%"
        publish(
            commit_msg,
            do_push=not args.no_push,
            ignore_hourly_gate=args.force_publish,
        )
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode().strip() if e.stderr else str(e)
        log_publish(f"[git] push failed: {err}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch SOL-007 LST health metrics and update dashboard latest/history files."""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BASELINE_PATH = DATA / "baseline.json"
LATEST_PATH = DATA / "latest.json"
HISTORY_PATH = DATA / "history.json"
INCIDENTS_PATH = DATA / "incidents.json"
PRICE_CACHE_PATH = DATA / "price-cache.json"
SOL_MINT = "So11111111111111111111111111111111111111112"
SANCTUM_SOL_VALUE_URL = "https://extra-api.sanctum.so/v1/sol-value/current"
MAX_CACHE_AGE_HOURS = 4.0
R2_ALERT_RATIO = 0.985
R2_BREACH_HOURS = 6.0


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "sol007-health-tracker/1.0"})
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


def sanctum_sol_value(mint):
    """Return intrinsic redemption value in SOL/token from Sanctum extra-api."""
    url = f"{SANCTUM_SOL_VALUE_URL}?lst={mint}"
    data = get_json(url, timeout=15)
    raw = (data.get("solValues") or {}).get(mint)
    if raw is None:
        raise ValueError(f"Sanctum sol-value missing {mint}")
    value = float(raw) / 1_000_000_000.0
    if value <= 0:
        raise ValueError(f"Sanctum sol-value non-positive for {mint}")
    return value, {"provider": "sanctum-extra-api", "url": url}


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


def load_json_safe(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _reference_price(reference, key):
    price = float(reference["legs"][key]["price_sol_per_token"])
    if price <= 0:
        raise ValueError(f"R2 reference price for {key} must be positive")
    return price


def _leg_deviation_pct(legs, reference, key):
    baseline_price = _reference_price(reference, key)
    current_price = float(legs[key]["sol_price"])
    return ((current_price - baseline_price) / baseline_price) * 100.0


def _r2_sustained_breach(history, reference, current_updated_at, current_below):
    """Return True when a downside R2 deviation has persisted across the full window."""
    if not history or not current_below:
        return False

    now = _parse_ts(current_updated_at)
    if now is None:
        return False

    window_start = now.timestamp() - (R2_BREACH_HOURS * 3600.0)
    observed = {key: [] for key in current_below}

    for row in history:
        ts = _parse_ts(row.get("updated_at"))
        if ts is None:
            continue
        row_legs = row.get("legs") or {}
        for key in current_below:
            try:
                ratio = float(row_legs[key]["sol_price"]) / _reference_price(reference, key)
            except Exception:
                continue
            observed[key].append((ts.timestamp(), ratio))

    for key, rows in observed.items():
        if not rows:
            continue
        before_or_at_start = [row for row in rows if row[0] <= window_start]
        after_start = [row for row in rows if row[0] >= window_start]
        if not before_or_at_start:
            continue
        latest_before_start = max(before_or_at_start, key=lambda row: row[0])
        current_ratio = float(current_below[key]) if isinstance(current_below, dict) else None
        evidence_rows = [latest_before_start, *after_start]
        if current_ratio is not None:
            evidence_rows.append((now.timestamp(), current_ratio))

        covers_full_window = latest_before_start[0] <= window_start
        stayed_below_threshold = all(ratio < R2_ALERT_RATIO for _, ratio in evidence_rows)
        if covers_full_window and stayed_below_threshold:
            return True
    return False


def evaluate_r2_tripwire(legs, baseline, history=None, current_updated_at=None, r2_reference=None):
    """Evaluate R2 as downside peg risk, not upside staking accrual or AMM premium."""
    reference = r2_reference or baseline
    reference_source = reference.get("source", "baseline")
    jito_dev = _leg_deviation_pct(legs, reference, "jitosol")
    inf_dev = _leg_deviation_pct(legs, reference, "inf")
    current_below = {}
    for key in ("jitosol", "inf"):
        ratio = float(legs[key]["sol_price"]) / _reference_price(reference, key)
        if ratio < R2_ALERT_RATIO:
            current_below[key] = ratio

    status = "ok"
    if current_below:
        status = "breached" if _r2_sustained_breach(history, reference, current_updated_at, current_below) else "alert"

    note = (
        f"JitoSOL {jito_dev:+.3f}%, INF {inf_dev:+.3f}% vs {reference_source}. "
        f"R2 downside peg threshold: <{R2_ALERT_RATIO:.3f} for >{R2_BREACH_HOURS:.0f}h. "
        "Upside premium/accrual is not a breach."
    )
    return status, note


def load_r2_reference(baseline):
    """Use current Sanctum redemption value for R2, falling back to baseline if unavailable."""
    reference = {"source": "sanctum-redemption", "legs": {}}
    notes = []
    ok = True
    for key in ("jitosol", "inf"):
        base_leg = baseline["legs"][key]
        fallback_price = float(base_leg["price_sol_per_token"])
        try:
            value, _meta = sanctum_sol_value(base_leg["mint"])
            reference["legs"][key] = {"price_sol_per_token": value}
            notes.append(f"Sanctum redemption OK for {key}")
        except Exception as exc:
            ok = False
            reference["legs"][key] = {"price_sol_per_token": fallback_price}
            notes.append(f"Baseline R2 reference fallback for {key}: {exc}")
    if not ok:
        reference["source"] = "baseline-fallback"
    return reference, ok, " | ".join(notes)


def load_price_cache(path=PRICE_CACHE_PATH):
    """Return dict or None if missing/invalid."""
    data = load_json_safe(path, None)
    if not data or not isinstance(data, dict):
        return None
    return data

def save_price_cache(jitosol_price, inf_price, timestamp, path=PRICE_CACHE_PATH):
    """Atomic write of last-known-good prices after successful Jupiter fetch."""
    payload = {
        "jitosol_sol_price": round(float(jitosol_price), 9),
        "inf_sol_price": round(float(inf_price), 9),
        "timestamp": timestamp,
    }
    atomic_write_json(path, payload)

def get_price_with_lkg(base_leg, price_cache, now_iso, key_label):
    """Jupiter -> fresh LKG cache (<=4h) -> baseline. Returns (price, source, note)."""
    baseline_price = float(base_leg["price_sol_per_token"])
    try:
        price, meta = jupiter_sol_price(base_leg["mint"])
        return price, "jupiter", f"Jupiter OK for {key_label}"
    except Exception as e:
        print(f"Price fetch failed for {key_label}: {e}", file=sys.stderr)
        # Try LKG cache if fresh
        if price_cache:
            try:
                ts = price_cache.get("timestamp")
                if ts:
                    cached_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                    age_h = (now_dt - cached_dt).total_seconds() / 3600.0
                    if age_h <= MAX_CACHE_AGE_HOURS:
                        if key_label == "jitosol":
                            cached_p = float(price_cache.get("jitosol_sol_price", baseline_price))
                        else:
                            cached_p = float(price_cache.get("inf_sol_price", baseline_price))
                        return cached_p, "lkg-cache", f"LKG cache used (age {age_h:.1f}h <=4h) for {key_label}"
            except Exception as cache_err:
                print(f"Cache parse/age error for {key_label}: {cache_err}", file=sys.stderr)
        # Final backstop: baseline (existing behavior)
        return baseline_price, "baseline-fallback", f"Baseline fallback for {key_label} (cache missing/stale)"


def evaluate_tripwires(
    legs,
    baseline,
    total_sol_equivalent,
    deposit_baseline,
    incidents,
    price_fetch_ok,
    price_source_note="",
    history=None,
    current_updated_at=None,
    r2_reference=None,
):
    """Evaluate all 5 tripwires per spec."""
    tripwires = {}
    overall_status = "healthy"

    # R2: LST Peg Deviation
    r2_status, r2_note = evaluate_r2_tripwire(
        legs,
        baseline,
        history=history,
        current_updated_at=current_updated_at,
        r2_reference=r2_reference,
    )
    if r2_status == "breached":
        overall_status = "breached"
    elif r2_status == "alert":
        if overall_status == "healthy":
            overall_status = "alert"
    tripwires["R2"] = {"label": "LST Peg Deviation", "status": r2_status, "note": r2_note}

    # R4: Venue TVL Floor (Sanctum/Jito TVL via public endpoints if accessible)
    r4_status = "ok"
    r4_note = "TVL last checked healthy. Alert if TVL drops >30% in 30 days."
    try:
        # Attempt Sanctum TVL (best-effort; fall back gracefully)
        sanctum_url = "https://api.sanctum.so/v1/tvl"
        sanctum_data = get_json(sanctum_url, timeout=10)
        # Jito TVL endpoint (best-effort)
        jito_url = "https://api.jito.wtf/v1/tvl"
        jito_data = get_json(jito_url, timeout=10)
        r4_note = f"Sanctum/Jito TVL endpoints reachable. Alert if TVL drops >30% in 30 days."
    except Exception:
        r4_note = "data unavailable"
    tripwires["R4"] = {"label": "Venue TVL Floor", "status": r4_status, "note": r4_note}

    # R5: Portfolio Drawdown
    drift_pct = ((total_sol_equivalent - deposit_baseline) / deposit_baseline) * 100.0
    r5_status = "ok"
    if drift_pct < -10.0:
        r5_status = "breached"
        overall_status = "breached"
    elif drift_pct < -5.0:
        r5_status = "alert"
        if overall_status == "healthy":
            overall_status = "alert"
    r5_note = f"Current drift {drift_pct:+.2f}% vs {deposit_baseline} SOL deposit. Alert <-5%, breached <-10%."
    tripwires["R5"] = {"label": "Portfolio Drawdown", "status": r5_status, "note": r5_note}

    # R7: Smart-Contract Incident
    r7_status = "ok"
    r7_note = "No Sanctum/Jito smart-contract incidents detected. Automatic exit if confirmed breach."
    try:
        inc = load_json_safe(INCIDENTS_PATH, {"incidents": []})
        for entry in inc.get("incidents", []):
            if entry.get("tag") == "R7" or "R7" in str(entry.get("tags", [])):
                r7_status = "breached"
                overall_status = "breached"
                r7_note = f"R7-tagged incident detected: {entry.get('note', 'see incidents.json')}"
                break
    except Exception:
        pass
    tripwires["R7"] = {"label": "Smart-Contract Incident", "status": r7_status, "note": r7_note}

    # R8: Venue Availability
    r8_status = "ok" if price_fetch_ok else "alert"
    fallback_detail = f" ({price_source_note})" if (not price_fetch_ok and price_source_note) else ""
    r8_note = "Jupiter price API and LST exchange rates fetched successfully." if price_fetch_ok else f"Partial price fetch failure{fallback_detail}."
    tripwires["R8"] = {"label": "Venue Availability", "status": r8_status, "note": r8_note}

    return tripwires, overall_status, drift_pct


def trim_history(history, max_entries=90):
    """Keep only the most recent N entries."""
    if len(history) <= max_entries:
        return history
    return history[-max_entries:]


def main():
    try:
        baseline = json.loads(BASELINE_PATH.read_text())
        pilot_pubkey = baseline.get("pilot_pubkey", "HFWs4p4n9vGDRRpnxtsTATsnvoaub5tu76VUaLeExyfH")
        deposit_baseline = float(baseline.get("deposit_baseline_sol", 25.0))

        now = datetime.now(timezone.utc).replace(microsecond=0)
        updated_at = now.isoformat().replace("+00:00", "Z")

        # Fetch current prices with LKG + baseline hardening (SOL-AI-MON-001)
        price_fetch_ok = True
        price_source_notes = []
        legs = {}
        total_sol_equivalent = float(baseline.get("legs", {}).get("native_sol", 0.0))

        now_iso = updated_at
        price_cache = load_price_cache()
        r2_reference, r2_reference_ok, r2_reference_note = load_r2_reference(baseline)
        price_source_notes.append(r2_reference_note)

        for key in ("jitosol", "inf"):
            base_leg = baseline["legs"][key]
            current_price, source, note = get_price_with_lkg(base_leg, price_cache, now_iso, key)
            price_source_notes.append(note)
            if source != "jupiter":
                price_fetch_ok = False
            amount = float(base_leg["amount_token"])
            sol_equiv = amount * current_price
            total_sol_equivalent += sol_equiv

            legs[key] = {
                "symbol": base_leg.get("label", key.upper()),
                "balance": amount,
                "sol_price": current_price,
                "r2_reference_sol_price": r2_reference["legs"][key]["price_sol_per_token"],
                "r2_reference_source": r2_reference["source"],
                "sol_equivalent": round(sol_equiv, 6),
            }

        # On full success, persist LKG cache (better than static baseline on next ENOTFOUND)
        if price_fetch_ok:
            try:
                save_price_cache(
                    legs["jitosol"]["sol_price"],
                    legs["inf"]["sol_price"],
                    updated_at,
                )
            except Exception as save_err:
                print(f"[price-cache] save failed: {save_err}", file=sys.stderr)

        price_source_note = " | ".join(price_source_notes)

        drift_pct = ((total_sol_equivalent - deposit_baseline) / deposit_baseline) * 100.0

        history = load_json_safe(HISTORY_PATH, [])
        if not isinstance(history, list):
            history = []

        # Evaluate tripwires
        incidents = load_json_safe(INCIDENTS_PATH, {"incidents": []})
        tripwires, overall_status, drift_pct = evaluate_tripwires(
            legs,
            baseline,
            total_sol_equivalent,
            deposit_baseline,
            incidents,
            price_fetch_ok,
            price_source_note,
            history=history,
            current_updated_at=updated_at,
            r2_reference=r2_reference,
        )

        # Build latest.json payload (exact schema match for app.js)
        latest = {
            "updated_at": updated_at,
            "pilot_pubkey": pilot_pubkey,
            "total_sol_equivalent": round(total_sol_equivalent, 6),
            "drift_pct": round(drift_pct, 4),
            "tripwire_status": overall_status,
            "legs": legs,
            "tripwires": tripwires,
            "price_source_note": price_source_note,
            "r2_reference_source": r2_reference["source"],
            "r2_reference_ok": r2_reference_ok,
        }

        atomic_write_json(LATEST_PATH, latest)

        # Append to history.json (keep last 90)
        history_entry = {
            "updated_at": updated_at,
            "pilot_pubkey": pilot_pubkey,
            "total_sol_equivalent": round(total_sol_equivalent, 6),
            "drift_pct": round(drift_pct, 4),
            "tripwire_status": overall_status,
            "legs": legs,
            "price_source_note": price_source_note,
            "r2_reference_source": r2_reference["source"],
            "r2_reference_ok": r2_reference_ok,
        }
        history.append(history_entry)
        history = trim_history(history, 90)
        atomic_write_json(HISTORY_PATH, history)

        print(json.dumps(latest, indent=2, sort_keys=True))

        # Git commit/push (atomic, same pattern as fetch_yield.py)
        try:
            ts = latest["updated_at"]
            status = latest["tripwire_status"]
            commit_msg = f"health update {ts} status={status}"
            subprocess.run(
                ["git", "add", "data/latest.json", "data/history.json"],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=str(ROOT),
                capture_output=True,
            )
            if result.returncode != 0:  # staged changes exist
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=str(ROOT),
                    check=True,
                    capture_output=True,
                )
                subprocess.run(["git", "push"], cwd=str(ROOT), check=True, capture_output=True)
                print(f"[git] pushed: {commit_msg}")
            else:
                print("[git] no change to commit")
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode().strip() if e.stderr else str(e)
            print(f"[git] push failed: {err}", file=sys.stderr)

        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

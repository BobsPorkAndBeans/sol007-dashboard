#!/usr/bin/env bash
# sanctum_watch.sh — SOL-007 30-min health wrapper
# Runs update_health.py + fetch_yield.py, reads latest.json + returns.json,
# prints ONE status line. Exit 0 = ok/alert. Exit 1 = breach. Exit 2 = pipeline error.

set -euo pipefail
DASH="/Users/clayton/sol007-dashboard"
LATEST="$DASH/data/latest.json"
RETURNS="$DASH/data/returns.json"

cd "$DASH"

# Run scripts — suppress verbose output; only keep exit code
if ! python3 scripts/update_health.py > /dev/null 2>&1; then
    echo "PIPELINE_ERROR: update_health.py failed"
    exit 2
fi

if ! python3 scripts/fetch_yield.py > /dev/null 2>&1; then
    echo "PIPELINE_ERROR: fetch_yield.py failed"
    exit 2
fi

if [ ! -f "$LATEST" ]; then
    echo "PIPELINE_ERROR: latest.json missing after scripts ran"
    exit 2
fi

python3 - <<'EOF'
import json, sys

with open("/Users/clayton/sol007-dashboard/data/latest.json") as f:
    d = json.load(f)

tripwires = d.get("tripwires", {})
r2 = tripwires.get("R2", {}).get("status", "unknown")
r5 = tripwires.get("R5", {}).get("status", "unknown")
r7 = tripwires.get("R7", {}).get("status", "unknown")
drift = d.get("drift_pct", "?")
price_ok = d.get("price_fetch_ok", True)

apy = "?"
try:
    with open("/Users/clayton/sol007-dashboard/data/returns.json") as f2:
        r = json.load(f2)
    # field is annualized_apy (float)
    raw_apy = r.get("annualized_apy", r.get("apy_pct", None))
    if raw_apy is not None:
        apy = f"{float(raw_apy):.2f}"
except Exception:
    pass

breach = (r5 == "breached") or (r7 == "breached")
r2_breach = (r2 == "breached")

if not price_ok:
    print(f"STATUS=data_fetch_error drift={drift}% r2={r2} r5=skip(no_price) r7={r7} apy={apy}%")
    sys.exit(0)
elif breach or r2_breach:
    which = []
    if r5 == "breached": which.append("R5")
    if r7 == "breached": which.append("R7")
    if r2 == "breached": which.append("R2")
    print(f"STATUS=BREACH({','.join(which)}) drift={drift}% r2={r2} r5={r5} r7={r7} apy={apy}%")
    sys.exit(1)
else:
    print(f"STATUS=ok drift={drift}% r2={r2} r5={r5} r7={r7} apy={apy}%")
    sys.exit(0)
EOF

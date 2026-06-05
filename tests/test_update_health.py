import importlib.util
from pathlib import Path


def load_update_health():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "update_health.py"
    spec = importlib.util.spec_from_file_location("update_health", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline():
    return {
        "deposit_baseline_sol": 25.0,
        "legs": {
            "jitosol": {"price_sol_per_token": 1.277},
            "inf": {"price_sol_per_token": 1.421},
        },
    }


def legs(jitosol_price, inf_price):
    return {
        "jitosol": {"sol_price": jitosol_price},
        "inf": {"sol_price": inf_price},
    }


def history_row(ts, jitosol_price, inf_price):
    return {
        "updated_at": ts,
        "legs": {
            "jitosol": {"sol_price": jitosol_price},
            "inf": {"sol_price": inf_price},
        },
    }


def test_r2_ignores_upside_premium_from_1251_context():
    update_health = load_update_health()

    status, note = update_health.evaluate_r2_tripwire(
        legs(1.2933559848944012, 1.4263555956436216),
        baseline(),
        history=[],
        current_updated_at="2026-06-05T18:53:17Z",
    )

    assert status == "ok"
    assert "JitoSOL +1.281%" in note
    assert "Upside premium/accrual is not a breach" in note


def test_r2_point_in_time_downside_move_alerts_without_breach():
    update_health = load_update_health()

    status, _ = update_health.evaluate_r2_tripwire(
        legs(1.2500, 1.4210),
        baseline(),
        history=[],
        current_updated_at="2026-06-05T18:53:17Z",
    )

    assert status == "alert"


def test_r2_sustained_downside_move_breaches_after_six_hours():
    update_health = load_update_health()
    hist = [
        history_row("2026-06-05T12:53:17Z", 1.2500, 1.4210),
        history_row("2026-06-05T15:53:17Z", 1.2490, 1.4210),
        history_row("2026-06-05T18:00:00Z", 1.2480, 1.4210),
    ]

    status, _ = update_health.evaluate_r2_tripwire(
        legs(1.2500, 1.4210),
        baseline(),
        history=hist,
        current_updated_at="2026-06-05T18:53:17Z",
    )

    assert status == "breached"

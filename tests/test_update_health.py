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


def r2_reference(jitosol_price=1.275866054, inf_price=1.407837461):
    return {
        "source": "onchain-lst-nav",
        "legs": {
            "jitosol": {"price_sol_per_token": jitosol_price},
            "inf": {"price_sol_per_token": inf_price},
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


def test_r2_uses_onchain_nav_reference_when_provided():
    update_health = load_update_health()

    status, note = update_health.evaluate_r2_tripwire(
        legs(1.2933559848944012, 1.4263555956436216),
        baseline(),
        history=[],
        current_updated_at="2026-06-05T18:53:17Z",
        r2_reference=r2_reference(),
    )

    assert status == "ok"
    assert "vs onchain-lst-nav" in note
    assert "JitoSOL +1.371%" in note


def test_r2_downside_move_alerts_against_nav_reference():
    update_health = load_update_health()

    status, _ = update_health.evaluate_r2_tripwire(
        legs(1.2500, 1.4210),
        baseline(),
        history=[],
        current_updated_at="2026-06-05T18:53:17Z",
        r2_reference=r2_reference(jitosol_price=1.275866054, inf_price=1.407837461),
    )

    assert status == "alert"


def test_r2_sustained_downside_includes_latest_row_before_boundary():
    update_health = load_update_health()
    hist = [
        history_row("2026-06-05T12:52:59Z", 1.2500, 1.4210),
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


# Live Jito NAV observed when Sanctum extra-api was frozen at 1.275866054.
LIVE_JITOSOL_NAV = 1.299244733
R2_ALERT_RATIO = 0.985
STALE_SANCTUM_JITOSOL = 1.275866054


def test_r2_alert_ratio_is_15_bps_true_discount():
    """R2_ALERT_RATIO stays 0.985 = -1.5% vs the live NAV reference."""
    update_health = load_update_health()
    assert update_health.R2_ALERT_RATIO == R2_ALERT_RATIO


def test_r2_true_15_pct_discount_vs_live_nav_alerts():
    """Market just below 0.985 * live NAV alerts; just above does not."""
    update_health = load_update_health()
    live_ref = r2_reference(jitosol_price=LIVE_JITOSOL_NAV, inf_price=1.45)
    trigger = R2_ALERT_RATIO * LIVE_JITOSOL_NAV  # ~1.279756

    status_alert, _ = update_health.evaluate_r2_tripwire(
        legs(trigger - 0.0001, 1.45),
        baseline(),
        history=[],
        current_updated_at="2026-09-03T17:00:00Z",
        r2_reference=live_ref,
    )
    status_ok, _ = update_health.evaluate_r2_tripwire(
        legs(trigger + 0.0001, 1.45),
        baseline(),
        history=[],
        current_updated_at="2026-09-03T17:00:00Z",
        r2_reference=live_ref,
    )
    assert status_alert == "alert"
    assert status_ok == "ok"


def test_old_stale_sanctum_threshold_was_327_pct_vs_live_nav():
    """Document the drift: 0.985 * frozen 1.275866054 == -3.27% vs live NAV."""
    old_trigger = R2_ALERT_RATIO * STALE_SANCTUM_JITOSOL
    true_discount = old_trigger / LIVE_JITOSOL_NAV - 1.0
    assert abs(true_discount * 100.0 - (-3.272)) < 0.01
    new_trigger = R2_ALERT_RATIO * LIVE_JITOSOL_NAV
    assert new_trigger > old_trigger


def test_onchain_nav_parsers_roundtrip_synthetic_accounts():
    import importlib.util
    import struct
    helper_path = Path(__file__).resolve().parents[1] / "scripts" / "lst_onchain_nav.py"
    spec = importlib.util.spec_from_file_location("lst_onchain_nav", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    jito_mint = helper.JITOSOL_MINT
    mint_bytes = helper.b58decode(jito_mint)[-32:]
    raw = bytearray(274)
    raw[helper.STAKE_POOL_MINT_OFFSET:helper.STAKE_POOL_MINT_OFFSET + 32] = mint_bytes
    struct.pack_into("<Q", raw, helper.STAKE_POOL_TOTAL_LAMPORTS_OFFSET, 1299244733)
    struct.pack_into("<Q", raw, helper.STAKE_POOL_TOKEN_SUPPLY_OFFSET, 1000000000)
    assert abs(helper.parse_spl_stake_pool_nav(bytes(raw), jito_mint) - 1.299244733) < 1e-12

    inf_mint = helper.INF_MINT
    inf_bytes = helper.b58decode(inf_mint)[-32:]
    pool = bytearray(176)
    struct.pack_into("<Q", pool, 0, 1448024050)
    pool[helper.INF_LP_TOKEN_MINT_OFFSET:helper.INF_LP_TOKEN_MINT_OFFSET + 32] = inf_bytes
    mint_acc = bytearray(44)
    struct.pack_into("<Q", mint_acc, helper.SPL_MINT_SUPPLY_OFFSET, 1000000000)
    assert abs(helper.parse_inf_pool_nav(bytes(pool), bytes(mint_acc), inf_mint) - 1.448024050) < 1e-12

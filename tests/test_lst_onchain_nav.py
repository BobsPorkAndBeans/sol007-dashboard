import importlib.util
import struct
from pathlib import Path


def load_helper():
    path = Path(__file__).resolve().parents[1] / "scripts" / "lst_onchain_nav.py"
    spec = importlib.util.spec_from_file_location("lst_onchain_nav", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jitosol_not_confused_with_inf_layout():
    h = load_helper()
    assert h.JITOSOL_STAKE_POOL != h.INF_POOL_STATE
    assert h.SPL_STAKE_POOL_PROGRAM != h.S_CONTROLLER_PROGRAM


def test_parse_rejects_mint_mismatch():
    h = load_helper()
    raw = bytearray(274)
    try:
        h.parse_spl_stake_pool_nav(bytes(raw), h.JITOSOL_MINT)
        raise AssertionError("expected mismatch")
    except ValueError as exc:
        assert "mint mismatch" in str(exc)

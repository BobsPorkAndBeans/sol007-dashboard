#!/usr/bin/env python3
"""Live on-chain LST NAV (SOL per token) via getAccountInfo only.

Sanctum extra-api /v1/sol-value/current has been frozen (JitoSOL 1.275866054,
INF 1.407837461) and must not be used as the basis/R2 denominator.

JitoSOL is a classic SPL stake pool:
    NAV = total_lamports / pool_token_supply
    pool: Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb
    program: SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy

INF is NOT an SPL stake pool. It is the Sanctum Infinity LP mint. NAV is
    total_sol_value / lp_mint.supply
read from s-controller PoolState
    AYhux5gJzCoeoc1PoJ1VxwPDe22RwcvpHviLDD1oCGvW
    program: 5ocnV1qiCgaQR8Jb8xWnVbApfaygJ8tNoZfgPwsgx9kx

Offsets (Borsh / packed Pod, verified on mainnet):
    SPL StakePool.pool_mint          @ 162
    SPL StakePool.total_lamports     @ 258
    SPL StakePool.pool_token_supply  @ 266
    PoolState.total_sol_value        @ 0   (u64)
    PoolState.lp_token_mint          @ 144
    SPL Mint.supply                  @ 36  (u64)

Read-only. No wallet, no capital, no writes.
"""
from __future__ import annotations

import json
import os
import struct
import urllib.request
from typing import Dict, List, Optional, Tuple

JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
INF_MINT = "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm"

JITOSOL_STAKE_POOL = "Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb"
SPL_STAKE_POOL_PROGRAM = "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy"

INF_POOL_STATE = "AYhux5gJzCoeoc1PoJ1VxwPDe22RwcvpHviLDD1oCGvW"
S_CONTROLLER_PROGRAM = "5ocnV1qiCgaQR8Jb8xWnVbApfaygJ8tNoZfgPwsgx9kx"

STAKE_POOL_MINT_OFFSET = 162
STAKE_POOL_TOTAL_LAMPORTS_OFFSET = 258
STAKE_POOL_TOKEN_SUPPLY_OFFSET = 266

INF_TOTAL_SOL_VALUE_OFFSET = 0
INF_LP_TOKEN_MINT_OFFSET = 144

SPL_MINT_SUPPLY_OFFSET = 36

_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

DEFAULT_RPCS = [
    os.environ.get("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
]


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s.encode("ascii"):
        n = n * 58 + _B58_ALPHABET.index(ch)
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    raw = b"\x00" * pad + body
    if len(raw) < 32:
        raw = b"\x00" * (32 - len(raw)) + raw
    return raw


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = bytearray()
    while n > 0:
        n, r = divmod(n, 58)
        out.append(_B58_ALPHABET[r])
    pad = 0
    for b in raw:
        if b == 0:
            pad += 1
        else:
            break
    return (_B58_ALPHABET[0:1] * pad + out[::-1]).decode("ascii")


def parse_spl_stake_pool_nav(raw: bytes, expected_mint: str) -> float:
    need = STAKE_POOL_TOKEN_SUPPLY_OFFSET + 8
    if len(raw) < need:
        raise ValueError(f"SPL stake pool account too small ({len(raw)} < {need})")
    pool_mint = raw[STAKE_POOL_MINT_OFFSET:STAKE_POOL_MINT_OFFSET + 32]
    if pool_mint != b58decode(expected_mint)[-32:]:
        raise ValueError(
            f"stake pool mint mismatch: got {b58encode(pool_mint)} expected {expected_mint}"
        )
    total_lamports = struct.unpack_from("<Q", raw, STAKE_POOL_TOTAL_LAMPORTS_OFFSET)[0]
    pool_token_supply = struct.unpack_from("<Q", raw, STAKE_POOL_TOKEN_SUPPLY_OFFSET)[0]
    if total_lamports <= 0 or pool_token_supply <= 0:
        raise ValueError(
            f"non-positive stake-pool NAV inputs total={total_lamports} supply={pool_token_supply}"
        )
    return total_lamports / pool_token_supply


def parse_spl_mint_supply(raw: bytes) -> int:
    if len(raw) < SPL_MINT_SUPPLY_OFFSET + 8:
        raise ValueError(f"SPL mint account too small ({len(raw)})")
    supply = struct.unpack_from("<Q", raw, SPL_MINT_SUPPLY_OFFSET)[0]
    if supply <= 0:
        raise ValueError("SPL mint supply is non-positive")
    return supply


def parse_inf_pool_nav(pool_raw: bytes, mint_raw: bytes, expected_mint: str) -> float:
    need = INF_LP_TOKEN_MINT_OFFSET + 32
    if len(pool_raw) < need:
        raise ValueError(f"INF PoolState too small ({len(pool_raw)} < {need})")
    lp_mint = pool_raw[INF_LP_TOKEN_MINT_OFFSET:INF_LP_TOKEN_MINT_OFFSET + 32]
    if lp_mint != b58decode(expected_mint)[-32:]:
        raise ValueError(
            f"INF lp_token_mint mismatch: got {b58encode(lp_mint)} expected {expected_mint}"
        )
    total_sol_value = struct.unpack_from("<Q", pool_raw, INF_TOTAL_SOL_VALUE_OFFSET)[0]
    supply = parse_spl_mint_supply(mint_raw)
    if total_sol_value <= 0:
        raise ValueError(f"INF total_sol_value non-positive: {total_sol_value}")
    return total_sol_value / supply


def rpc_get_account_info(pubkey: str, rpc_url: str, timeout: int = 12) -> Tuple[bytes, str]:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [pubkey, {"encoding": "base64", "commitment": "confirmed"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "sol007-onchain-lst-nav/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    if body.get("error"):
        raise ValueError(f"RPC error for {pubkey}: {body['error']}")
    value = (body.get("result") or {}).get("value")
    if not value:
        raise ValueError(f"getAccountInfo returned null for {pubkey}")
    data_b64 = (value.get("data") or [None])[0]
    if not data_b64:
        raise ValueError(f"getAccountInfo missing data for {pubkey}")
    import base64

    return base64.b64decode(data_b64), value.get("owner") or ""


def _get_account(pubkey: str, rpc_urls: Optional[List[str]] = None) -> Tuple[bytes, str, str]:
    urls = [u for u in (rpc_urls or DEFAULT_RPCS) if u]
    last_err: Optional[Exception] = None
    for url in urls:
        try:
            raw, owner = rpc_get_account_info(pubkey, url)
            return raw, owner, url
        except Exception as exc:
            last_err = exc
            continue
    raise ValueError(f"getAccountInfo failed for {pubkey}: {last_err}")


def fetch_lst_nav(mint: str, rpc_urls: Optional[List[str]] = None) -> Tuple[float, dict]:
    """Return (sol_per_token, meta) for JitoSOL or INF."""
    if mint == JITOSOL_MINT:
        raw, owner, url = _get_account(JITOSOL_STAKE_POOL, rpc_urls)
        if owner != SPL_STAKE_POOL_PROGRAM:
            raise ValueError(f"Jito pool owner {owner} != {SPL_STAKE_POOL_PROGRAM}")
        nav = parse_spl_stake_pool_nav(raw, JITOSOL_MINT)
        return nav, {
            "provider": "onchain-spl-stake-pool",
            "account": JITOSOL_STAKE_POOL,
            "owner": owner,
            "rpc": url,
            "method": "getAccountInfo",
        }
    if mint == INF_MINT:
        pool_raw, pool_owner, url = _get_account(INF_POOL_STATE, rpc_urls)
        if pool_owner != S_CONTROLLER_PROGRAM:
            raise ValueError(f"INF pool owner {pool_owner} != {S_CONTROLLER_PROGRAM}")
        mint_raw, _mint_owner, _ = _get_account(INF_MINT, rpc_urls)
        nav = parse_inf_pool_nav(pool_raw, mint_raw, INF_MINT)
        return nav, {
            "provider": "onchain-infinity-pool-state",
            "account": INF_POOL_STATE,
            "owner": pool_owner,
            "rpc": url,
            "method": "getAccountInfo",
        }
    raise ValueError(f"unsupported LST mint for on-chain NAV: {mint}")


def fetch_lst_navs(mints: List[str], rpc_urls: Optional[List[str]] = None) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for mint in mints:
        nav, _meta = fetch_lst_nav(mint, rpc_urls)
        out[mint] = nav
    return out

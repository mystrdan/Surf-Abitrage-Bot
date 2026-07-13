"""
DeDust price fetcher.

Provides:
  - get_price(session)                    : GRAM/USDT price (legacy)
  - get_all_pair_prices(session, base)    : dict[jetton_symbol -> price] for
                                            every base/jetton pool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

NAME: str = "DeDust"
POOLS_URL: str = "https://api.dedust.io/v2/pools"

_GRAM_DECIMALS: float = 1e9   # TON-side decimals
_USDT_DECIMALS: float = 1e6   # USDT side decimals
_REQUEST_TIMEOUT: float = 10.0

# Per-jetton decimals. Anything not listed here is assumed to use TON's 9
# decimals on the DeDust side. Add more as you need them.
_DECIMALS: dict[str, int] = {
    "USDT": 6,
    "USDC": 6,
    "JUSDT": 6,
    "NOT": 9,
    "HMSTR": 9,
    "STG": 9,
    "GRAM": 9,
}


def _decimals_for(symbol: str) -> int:
    return _DECIMALS.get(symbol.upper(), 9)


def _pool_implied_price(pool: dict[str, Any], base: str) -> float | None:
    """Return base_token price quoted in the other side, for pools that
    contain base. Returns None for pools that don't contain `base`."""
    if not isinstance(pool, dict):
        return None
    assets = pool.get("assets")
    reserves = pool.get("reserves")
    if not isinstance(assets, list) or not isinstance(reserves, list):
        return None
    if len(assets) != 2 or len(reserves) != 2:
        return None

    symbols: list[str] = []
    for a in assets:
        if not isinstance(a, dict):
            symbols.append("")
            continue
        meta = a.get("metadata") or {}
        symbols.append(str(meta.get("symbol", "")).upper())

    base = base.upper()
    if base not in symbols:
        return None

    try:
        base_idx = symbols.index(base)
        other_idx = 1 - base_idx
        base_reserve = float(reserves[base_idx])  / (10 ** _decimals_for(base))
        other_reserve = float(reserves[other_idx]) / (10 ** _decimals_for(symbols[other_idx]))
    except (ValueError, TypeError, IndexError):
        return None

    if base_reserve <= 0 or other_reserve <= 0:
        return None
    return other_reserve / base_reserve


def _extract_price(payload: Any) -> float | None:
    """Legacy: GRAM/USDT price only."""
    if isinstance(payload, dict):
        pools = payload.get("pools") or payload.get("pool_list") or []
    elif isinstance(payload, list):
        pools = payload
    else:
        return None
    if not isinstance(pools, list):
        return None
    for pool in pools:
        p = _pool_implied_price(pool, "GRAM")
        if p and p > 0:
            # First GRAM pair we see; in practice this is GRAM/USDT
            return p
    return None


async def get_price(session: aiohttp.ClientSession) -> float | None:
    """Return current GRAM/USDT price on DeDust, or None on failure."""
    if session.closed:
        return None
    try:
        async with session.get(
            POOLS_URL, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.warning("DeDust HTTP %s for %s", resp.status, POOLS_URL)
                return None
            data = await resp.json()
    except asyncio.TimeoutError:
        log.warning("[timeout] dedust.get_price: %s", POOLS_URL)
        return None
    except aiohttp.ClientError as e:
        log.warning("[network] dedust.get_price: %s", e)
        return None
    except RuntimeError as e:
        if "Session is closed" in str(e):
            log.debug("dedust.get_price: %s", e)
        else:
            log.warning("dedust.get_price: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("[error] dedust.get_price: %s", e)
        return None

    return _extract_price(data)


async def get_all_pair_prices(
    session: aiohttp.ClientSession, base_token: str
) -> dict[str, float]:
    """Return {jetton_symbol: price_in_base} for every pool that contains base."""
    if session.closed:
        return {}
    try:
        async with session.get(
            POOLS_URL, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.warning("DeDust HTTP %s for %s", resp.status, POOLS_URL)
                return {}
            data = await resp.json()
    except asyncio.TimeoutError:
        log.warning("[timeout] dedust.get_all_pair_prices: %s", POOLS_URL)
        return {}
    except aiohttp.ClientError as e:
        log.warning("[network] dedust.get_all_pair_prices: %s", e)
        return {}
    except RuntimeError as e:
        if "Session is closed" in str(e):
            log.debug("dedust.get_all_pair_prices: %s", e)
        else:
            log.warning("dedust.get_all_pair_prices: %s", e)
        return {}
    except Exception as e:  # noqa: BLE001
        log.exception("[error] dedust.get_all_pair_prices: %s", e)
        return {}

    if isinstance(data, dict):
        pools = data.get("pools") or data.get("pool_list") or []
    elif isinstance(data, list):
        pools = data
    else:
        pools = []
    if not isinstance(pools, list):
        return {}

    out: dict[str, float] = {}
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        assets = pool.get("assets")
        if not isinstance(assets, list) or len(assets) != 2:
            continue
        # Identify the non-base side's symbol
        symbols: list[str] = []
        for a in assets:
            meta = (a.get("metadata") or {}) if isinstance(a, dict) else {}
            symbols.append(str(meta.get("symbol", "")).upper())
        if base_token.upper() not in symbols:
            continue
        other_symbol = symbols[0] if symbols[1] == base_token.upper() else symbols[1]
        price = _pool_implied_price(pool, base_token)
        if price is not None and price > 0:
            out[other_symbol] = price

    return out

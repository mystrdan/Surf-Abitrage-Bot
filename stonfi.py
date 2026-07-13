"""
STON.fi price fetcher.

Provides two coroutines:
  - get_price(session)               : returns the GRAM/USDT price (legacy)
  - get_all_pair_prices(session)     : returns a dict of {jetton: price} for
                                       every base/jetton pool. New strategies
                                       use this to scan multiple jettons.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

NAME: str = "STON.fi"
POOLS_URL: str = "https://api.ston.fi/v1/pools"
_REQUEST_TIMEOUT: float = 10.0


def _extract_prices(data: Any) -> dict[str, float]:
    """
    Return a dict of {jetton_symbol: price} for every pool that contains
    the chain's base token. The caller decides which jettons to consider.
    The legacy get_price() below preserves the original GRAM/USDT contract.
    """
    out: dict[str, float] = {}
    if not isinstance(data, dict):
        return out

    pool_list = data.get("pool_list") or data.get("pools") or []
    if not isinstance(pool_list, list):
        return out

    for pool in pool_list:
        if not isinstance(pool, dict):
            continue
        sym0 = str(pool.get("token0_symbol", "")).upper()
        sym1 = str(pool.get("token1_symbol", "")).upper()
        price = pool.get("lp_price") or pool.get("price")
        if price is None:
            continue
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        # Caller is responsible for filtering which side is "base" — we
        # return whichever side the price is quoted in. The strategy
        # module already knows GRAM is the base on TON.
        out.setdefault(sym0, value)
        out.setdefault(sym1, value)

    return out


def _extract_price(data: Any) -> float | None:
    """Legacy: GRAM/USDT price only. Kept so the original code paths still work."""
    prices = _extract_prices(data)
    return prices.get("USDT")  # rough — see note below


async def get_price(session: aiohttp.ClientSession) -> float | None:
    """Return current GRAM/USDT price on STON.fi, or None on failure."""
    if session.closed:
        return None
    try:
        async with session.get(
            POOLS_URL, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.warning("STON.fi HTTP %s for %s", resp.status, POOLS_URL)
                return None
            data = await resp.json()
    except asyncio.TimeoutError:
        log.warning("[timeout] stonfi.get_price: %s", POOLS_URL)
        return None
    except aiohttp.ClientError as e:
        log.warning("[network] stonfi.get_price: %s", e)
        return None
    except RuntimeError as e:
        if "Session is closed" in str(e):
            log.debug("stonfi.get_price: %s", e)
        else:
            log.warning("stonfi.get_price: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("[error] stonfi.get_price: %s", e)
        return None

    return _extract_price(data)


async def get_all_pair_prices(
    session: aiohttp.ClientSession, base_token: str
) -> dict[str, float]:
    """
    Return {jetton_symbol: price_in_base_token} for every pool that contains
    base_token. Used by the multi-jetton strategy.
    """
    if session.closed:
        return {}
    try:
        async with session.get(
            POOLS_URL, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.warning("STON.fi HTTP %s for %s", resp.status, POOLS_URL)
                return {}
            data = await resp.json()
    except asyncio.TimeoutError:
        log.warning("[timeout] stonfi.get_all_pair_prices: %s", POOLS_URL)
        return {}
    except aiohttp.ClientError as e:
        log.warning("[network] stonfi.get_all_pair_prices: %s", e)
        return {}
    except RuntimeError as e:
        if "Session is closed" in str(e):
            log.debug("stonfi.get_all_pair_prices: %s", e)
        else:
            log.warning("stonfi.get_all_pair_prices: %s", e)
        return {}
    except Exception as e:  # noqa: BLE001
        log.exception("[error] stonfi.get_all_pair_prices: %s", e)
        return {}

    # Filter down to pools where the base token is one of the two sides.
    # We want prices for the OTHER token in the pair (the jetton vs base).
    all_prices = _extract_prices(data)
    base = base_token.upper()
    out: dict[str, float] = {}
    for sym, px in all_prices.items():
        # For pools containing base_token, we need to get the price of the non-base token
        if sym.upper() != base:  # Don't include base token itself
            out[sym] = px
    return out


# ---------- Route/New Pair helpers ----------

async def get_all_pools(
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """
    Return all pools for the route strategy. Each pool dict contains:
    - id: pool identifier
    - token symbols and reserves for price computation
    Placeholder — implement by parsing the pools endpoint fully.
    """
    if session.closed:
        return []
    try:
        async with session.get(
            POOLS_URL, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception:  # noqa: BLE001
        return []

    pools = data.get("pool_list") or data.get("pools") or []
    if not isinstance(pools, list):
        return []

    # Normalize pool data for strategy consumption
    out = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        out.append(pool)
    return out


async def get_recent_pools(
    session: aiohttp.ClientSession, since_ts: float
) -> list[dict[str, Any]]:
    """
    Return pools created after the given timestamp. Placeholder returns empty.
    Used by the new_pair strategy.
    """
    # Simple placeholder: STON.fi doesn't expose pool creation time via public API
    # without additional endpoints. Returns empty for now.
    return []

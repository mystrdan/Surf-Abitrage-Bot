"""
Strategy framework.

A Strategy is anything with a `name` class attribute and an async `find()`
method that takes (session, chain, chain_client, dexes, base_token) and
returns a list of Opportunity objects. Strategies are independent — adding
a new one is two lines: a class + a STRATEGY_REGISTRY entry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import aiohttp

import config

log = logging.getLogger(__name__)


# ---------- Opportunity ----------

@dataclass
class Opportunity:
    chain: str
    label: str
    route: list[str]                 # e.g. ["GRAM", "USDT", "GRAM"]
    profit_pct: float
    estimated_slippage_pct: float
    liquidity_usd: float
    required_balance: float
    wallet_address: str
    legs: list[dict] = field(default_factory=list)


# ---------- Strategy protocol (duck-typed) ----------

class Strategy(Protocol):
    name: str

    async def find(
        self,
        session: aiohttp.ClientSession,
        chain: str,
        chain_client: Any,
        dexes: list[Any],
        base_token: str,
    ) -> list[Opportunity]: ...


# ---------- Helpers ----------

def net_profit_pct(buy: float, sell: float, buy_fee: float, sell_fee: float) -> float:
    if buy <= 0:
        return 0.0
    gross = ((sell - buy) / buy) * 100.0
    return gross - buy_fee - sell_fee - config.SLIPPAGE


async def _safe_get_all_pair_prices(dex, session, base_token) -> dict[str, float]:
    """Use the new multi-pair fetcher if a DEX has it; otherwise empty dict."""
    if not hasattr(dex, "get_all_pair_prices"):
        return {}
    if session.closed:
        return {}
    try:
        return await dex.get_all_pair_prices(session, base_token)
    except Exception as e:  # noqa: BLE001
        log.debug("%s: get_all_pair_prices raised: %s", dex.NAME, e)
        return {}


# ---------- Strategy 1: Multi-DEX, Multi-Jetton Arbitrage ----------

class CrossDexArbitrage:
    """
    For every configured jetton, compare the base_token's price across
    DEXes on a chain. Returns the BEST profitable (jetton, dex_buy, dex_sell)
    triple as a single Opportunity. The engine in main.py already picks the
    overall best across all strategies, so this is fine.
    """

    name = "cross_dex"

    async def find(self, session, chain, chain_client, dexes, base_token):
        if len(dexes) < 2 or session.closed:
            return []

        # Pull all base-token prices from every DEX concurrently.
        tasks = {
            d: asyncio.create_task(
                _safe_get_all_pair_prices(d, session, base_token)
            )
            for d in dexes
        }
        dex_prices: dict[str, dict[str, float]] = {}
        for d, t in tasks.items():
            try:
                dex_prices[d.NAME] = await t
            except Exception as e:  # noqa: BLE001
                log.debug("%s: get_all_pair_prices raised: %s", d.NAME, e)
                dex_prices[d.NAME] = {}

        # Build the set of jettons that are priced on at least two DEXes.
        # We only scan jettons the user has whitelisted in config.
        candidate_jettons: set[str] = set()
        whitelisted = {j.upper() for j in config.INTERMEDIATE_JETTONS}
        for syms in dex_prices.values():
            candidate_jettons.update(syms.keys())
        candidate_jettons &= whitelisted
        # Always include USDT even if not whitelisted — it's the safest pair.
        candidate_jettons.add("USDT")
        if not candidate_jettons:
            return []

        best: Opportunity | None = None
        size = config.TRADE_SIZE

        for jetton in candidate_jettons:
            # Find best buy (lowest) and best sell (highest) for this jetton.
            buy_dex = buy_price = None
            sell_dex = sell_price = None
            for dex in dexes:
                p = dex_prices.get(dex.NAME, {}).get(jetton)
                if p is None or p <= 0:
                    continue
                if buy_price is None or p < buy_price:
                    buy_dex, buy_price = dex.NAME, p
                if sell_price is None or p > sell_price:
                    sell_dex, sell_price = dex.NAME, p
            if not buy_dex or not sell_dex or buy_dex == sell_dex:
                continue

            buy_fee = config.DEX_FEES.get(buy_dex, 0.0)
            sell_fee = config.DEX_FEES.get(sell_dex, 0.0)
            profit = net_profit_pct(buy_price, sell_price, buy_fee, sell_fee)
            if profit < config.MIN_PROFIT:
                continue

            opp = Opportunity(
                chain=chain,
                label=(
                    f"{jetton}: BUY {buy_dex} @ {buy_price:.6f} → "
                    f"SELL {sell_dex} @ {sell_price:.6f}"
                ),
                route=[base_token, jetton, base_token],
                profit_pct=profit,
                estimated_slippage_pct=config.SLIPPAGE,
                # Rough liquidity estimate from trade size. A real impl would
                # query pool reserves directly.
                liquidity_usd=size * buy_price * 2,
                required_balance=size,
                wallet_address=chain_client.wallet_address,
                legs=[
                    {"dex": buy_dex,  "side": "buy",  "token_in": base_token,
                     "token_out": jetton, "amount_in": size},
                    {"dex": sell_dex, "side": "sell", "token_in": jetton,
                     "token_out": base_token, "amount_in": size * buy_price},
                ],
            )
            if best is None or opp.profit_pct > best.profit_pct:
                best = opp

        return [best] if best else []


# Backwards-compat helper used by main.py / other strategies
async def _safe_get_price(dex, session) -> float | None:
    if not hasattr(dex, "get_price"):
        return None
    if session.closed:
        return None
    try:
        return await dex.get_price(session)
    except Exception as e:  # noqa: BLE001
        log.debug("%s: get_price raised: %s", dex.NAME, e)
        return None


# ---------- Strategy 2: Route Arbitrage ----------

class RouteArbitrage:
    """
    Find profitable round-trip routes within a single DEX, e.g.
    GRAM → X → GRAM or GRAM → X → Y → GRAM.

    Requires each DEX to expose a `get_all_pools(session)` coroutine that
    returns a list of pool dicts (with token symbols + reserves). Returns
    no opportunities for any DEX that doesn't implement it.
    """

    name = "route"

    async def find(self, session, chain, chain_client, dexes, base_token):
        opps: list[Opportunity] = []
        for dex in dexes:
            if not hasattr(dex, "get_all_pools"):
                continue
            try:
                pools = await dex.get_all_pools(session)
            except Exception as e:  # noqa: BLE001
                log.debug("%s: get_all_pools failed: %s", dex.NAME, e)
                continue
            # Real implementation: build a base→X→base graph, compute
            # round-trip prices, emit opportunities where
            # net_profit_pct >= MIN_PROFIT. Placeholder returns nothing.
        return opps


# ---------- Strategy 3: New Pair Hunter ----------

class NewPairHunter:
    """
    Watch for newly created pools on each DEX and emit opportunities only
    if they pass configured safety rules. Each new pool ID is remembered
    in memory so the bot doesn't re-evaluate the same pool every cycle.
    """

    name = "new_pair"

    def __init__(self) -> None:
        self.seen: set[str] = set()

    async def find(self, session, chain, chain_client, dexes, base_token):
        opps: list[Opportunity] = []
        cutoff = time.time() - 3600
        for dex in dexes:
            if not hasattr(dex, "get_recent_pools"):
                continue
            try:
                recent = await dex.get_recent_pools(session, since_ts=cutoff)
            except Exception as e:  # noqa: BLE001
                log.debug("%s: get_recent_pools failed: %s", dex.NAME, e)
                continue
            for pool in recent:
                pid = pool.get("id") or pool.get("address")
                if not pid or pid in self.seen:
                    continue
                self.seen.add(pid)
        return opps


# ---------- Registry ----------

STRATEGY_REGISTRY: dict[str, type] = {
    "cross_dex": CrossDexArbitrage,
    "route": RouteArbitrage,
    "new_pair": NewPairHunter,
}


def build_strategies(names: list[str]) -> list[Any]:
    out: list[Any] = []
    for n in names:
        cls = STRATEGY_REGISTRY.get(n)
        if cls is None:
            log.warning("Unknown strategy %r, skipping", n)
            continue
        out.append(cls())
    return out

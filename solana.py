"""
Solana chain helper. Mirrors the ton.py interface so the engine can treat
both chains uniformly. Fill in get_wallet_balance / simulate_route /
execute_route when you add Solana DEX adapters.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


class SolanaClient:
    def __init__(self, rpc_url: str, private_key: str = "") -> None:
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.wallet_address: str = ""

    async def get_wallet_balance(
        self, session: aiohttp.ClientSession, address: str
    ) -> float:
        log.debug("SolanaClient.get_wallet_balance: not yet implemented")
        return 0.0

    async def simulate_route(
        self, session: aiohttp.ClientSession, opp: Any
    ) -> tuple[bool, str]:
        raise NotImplementedError("SolanaClient.simulate_route: not implemented")

    async def execute_route(self, opp: Any) -> Any:
        raise NotImplementedError("SolanaClient.execute_route: not implemented")

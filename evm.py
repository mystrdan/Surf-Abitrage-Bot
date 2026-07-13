"""
EVM chain helper. Base and BNB Chain share the same JSON-RPC interface, so
one client class covers both. The only differences are the RPC URL and the
chain_id; both are passed in at construction.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


class EvmClient:
    def __init__(self, rpc_url: str, chain_id: int, private_key: str = "") -> None:
        self.rpc_url = rpc_url
        self.chain_id = chain_id
        self.private_key = private_key
        self.wallet_address: str = ""

    async def get_wallet_balance(
        self, session: aiohttp.ClientSession, address: str
    ) -> float:
        log.debug("EvmClient(chain_id=%d).get_wallet_balance: not implemented", self.chain_id)
        return 0.0

    async def simulate_route(
        self, session: aiohttp.ClientSession, opp: Any
    ) -> tuple[bool, str]:
        raise NotImplementedError(
            f"EvmClient(chain_id={self.chain_id}).simulate_route: not implemented"
        )

    async def execute_route(self, opp: Any) -> Any:
        raise NotImplementedError(
            f"EvmClient(chain_id={self.chain_id}).execute_route: not implemented"
        )

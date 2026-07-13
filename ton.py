"""
Minimal TON chain helper. The two read paths (get_wallet_balance) and the
two on-chain paths (simulate_route, execute_route) follow the same pattern:
work now where possible, raise NotImplementedError for the parts that need
your real wallet and DEX router wiring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

_NANOTONS_PER_TON: float = 1e9
_REQUEST_TIMEOUT: float = 10.0


class TonClient:
    """Async wrapper around a TON JSON-RPC endpoint."""

    def __init__(self, rpc_url: str, private_key: str = "") -> None:
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.wallet_address: str = ""

    async def get_wallet_balance(
        self, session: aiohttp.ClientSession, address: str
    ) -> float:
        """Return the wallet's balance in TON, or 0.0 on any failure."""
        if not address:
            log.warning("get_wallet_balance called with empty address")
            return 0.0
        if session.closed:
            log.debug("get_wallet_balance: session already closed, skipping")
            return 0.0

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAddressBalance",
            "params": {"address": address},
        }
        try:
            async with session.post(
                self.rpc_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    log.warning("get_wallet_balance HTTP %s for %s", resp.status, address)
                    return 0.0
                data = await resp.json()
        except asyncio.TimeoutError:
            log.warning(
                "[timeout] get_wallet_balance: %s did not respond in %.1fs",
                self.rpc_url, _REQUEST_TIMEOUT,
            )
            return 0.0
        except aiohttp.ClientError as e:
            log.warning("[network] get_wallet_balance: %s", e)
            return 0.0
        except RuntimeError as e:
            if "Session is closed" in str(e):
                log.debug("get_wallet_balance: %s", e)
            else:
                log.warning("get_wallet_balance: %s", e)
            return 0.0
        except Exception as e:  # noqa: BLE001
            log.exception("[error] get_wallet_balance: %s", e)
            return 0.0

        if not data.get("ok"):
            log.warning("get_wallet_balance RPC error: %s", data.get("error"))
            return 0.0

        try:
            nano = int(data["result"])
        except (KeyError, ValueError, TypeError) as e:
            log.warning("get_wallet_balance unexpected payload: %s (%s)", data, e)
            return 0.0
        return nano / _NANOTONS_PER_TON if nano >= 0 else 0.0

    async def send_swap(self, dex_name: str, side: str, amount: float) -> None:
        raise NotImplementedError(
            f"send_swap not implemented: {side} {amount} on {dex_name}"
        )

    async def simulate_route(
        self, session: aiohttp.ClientSession, opp: Any
    ) -> tuple[bool, str]:
        raise NotImplementedError(
            "TonClient.simulate_route: add per-leg DEX simulation before "
            "enabling AUTO_TRADE."
        )

    async def execute_route(self, opp: Any) -> Any:
        raise NotImplementedError(
            "TonClient.execute_route: add real signing/broadcast logic before "
            "enabling AUTO_TRADE."
        )

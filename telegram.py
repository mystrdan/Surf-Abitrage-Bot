"""
Telegram notifier. Silent no-op when TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID
is empty. Uses the shared aiohttp session — no extra connection pool.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self._enabled = bool(token and chat_id)
        if not self._enabled:
            log.info("Telegram not configured — notifications disabled")

    def set_session(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _send(self, text: str) -> None:
        if not self._enabled:
            return
        session = getattr(self, "_session", None)
        if session is None or session.closed:
            return
        try:
            url = _API.format(token=self.token)
            async with session.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("Telegram send HTTP %s", resp.status)
        except aiohttp.ClientError as e:
            log.warning("Telegram send network error: %s", e)
        except RuntimeError as e:
            if "Session is closed" in str(e):
                log.debug("Telegram send: %s", e)
            else:
                log.warning("Telegram send: %s", e)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram send failed: %s", e)

    async def trade_executed(self, record: Any) -> None:
        await self._send((
            "✅ <b>Trade Executed</b>\n"
            f"Chain: {record.chain}\n"
            f"Side: {record.side}\n"
            f"In: {record.amount_in:.6f}\n"
            f"Out: {record.amount_out:.6f}\n"
            f"Profit: {record.profit_pct:+.2f}%"
        ))

    async def trade_failed(self, opp: Any, reason: str) -> None:
        await self._send((
            "❌ <b>Trade Failed</b>\n"
            f"Chain: {opp.chain}\n"
            f"Opportunity: {opp.label}\n"
            f"Reason: {reason}"
        ))

    async def profit(self, record: Any) -> None:
        await self._send((
            "💰 <b>Profit</b>\n"
            f"Chain: {record.chain}\n"
            f"Profit: {record.profit_pct:+.2f}%"
        ))

    async def daily_summary(self, ledger: Any) -> None:
        recs = ledger.today_records()
        if not recs:
            msg = "📊 <b>Daily Summary</b>\nNo trades today."
        else:
            total = ledger.total_profit_pct()
            msg = (
                "📊 <b>Daily Summary</b>\n"
                f"Trades: {len(recs)}\n"
                f"Cumulative profit: {total:+.2f}%"
            )
        await self._send(msg)

    async def critical_error(self, message: str) -> None:
        await self._send(f"🚨 <b>Critical Error</b>\n{message}")

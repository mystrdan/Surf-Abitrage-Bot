"""
Lightweight multi-chain, multi-DEX, multi-strategy trading bot for Termux.

    python main.py

Ctrl+C to stop. No database, no server, no dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Final

import aiohttp

import config
import stonfi
import dedust
from ton import TonClient
from solana import SolanaClient
from evm import EvmClient
from strategies import build_strategies, Opportunity
from telegram import TelegramNotifier

log = logging.getLogger("scanner")

TON_DEXES:    Final[list] = [stonfi, dedust]
SOLANA_DEXES: Final[list] = []
BASE_DEXES:   Final[list] = []
BNB_DEXES:    Final[list] = []

CHAIN_DEXES: Final[dict[str, list]] = {
    "TON":    TON_DEXES,
    "Solana": SOLANA_DEXES,
    "Base":   BASE_DEXES,
    "BNB":    BNB_DEXES,
}


@dataclass
class TradeRecord:
    chain: str
    dex: str
    side: str
    amount_in: float
    amount_out: float
    profit_pct: float
    timestamp: float


class TradeLedger:
    def __init__(self) -> None:
        self.records: list[TradeRecord] = []
        self._today: str = time.strftime("%Y-%m-%d")

    def record(self, r: TradeRecord) -> None:
        self.records.append(r)

    def today_records(self) -> list[TradeRecord]:
        d = time.strftime("%Y-%m-%d")
        return [r for r in self.records
                if time.strftime("%Y-%m-%d", time.localtime(r.timestamp)) == d]

    def total_profit_pct(self) -> float:
        recs = self.today_records()
        if not recs:
            return 0.0
        ratio = 1.0
        for r in recs:
            ratio *= (1.0 + r.profit_pct / 100.0)
        return (ratio - 1.0) * 100.0

    def new_day(self) -> bool:
        d = time.strftime("%Y-%m-%d")
        if d != self._today:
            self._today = d
            return True
        return False


@dataclass
class SafetyReport:
    passed: bool
    reasons: list[str]


async def run_safety_checks(
    opp: Opportunity, chain_client: Any, session: aiohttp.ClientSession
) -> SafetyReport:
    reasons: list[str] = []
    if opp.profit_pct < config.MIN_PROFIT:
        reasons.append(f"profit {opp.profit_pct:.2f}% < MIN_PROFIT {config.MIN_PROFIT:.2f}%")
    if opp.liquidity_usd < config.MIN_LIQUIDITY_USD:
        reasons.append(f"liquidity ${opp.liquidity_usd:.0f} < MIN_LIQUIDITY_USD ${config.MIN_LIQUIDITY_USD:.0f}")
    if opp.estimated_slippage_pct > config.SLIPPAGE:
        reasons.append(f"slippage {opp.estimated_slippage_pct:.2f}% > SLIPPAGE {config.SLIPPAGE:.2f}%")
    if not opp.route or len(opp.route) < 2:
        reasons.append("route invalid or empty")

    if opp.wallet_address:
        try:
            bal = await chain_client.get_wallet_balance(session, opp.wallet_address)
        except Exception as e:  # noqa: BLE001
            reasons.append(f"balance lookup failed: {e}")
            bal = 0.0
        if bal < opp.required_balance:
            reasons.append(f"balance {bal:.4f} < required {opp.required_balance:.4f}")
    elif config.AUTO_TRADE:
        reasons.append("wallet_address not configured on chain client")

    try:
        ok, msg = await chain_client.simulate_route(session, opp)
        if not ok:
            reasons.append(f"simulation failed: {msg}")
    except NotImplementedError as e:
        reasons.append(f"simulation not implemented: {e}")
    except Exception as e:  # noqa: BLE001
        reasons.append(f"simulation error: {e}")

    return SafetyReport(passed=not reasons, reasons=reasons)


def _build_chain_clients() -> dict[str, Any]:
    clients: dict[str, Any] = {}
    if "TON" in config.ENABLED_CHAINS:
        clients["TON"] = TonClient(config.CHAIN_RPC["TON"], config.PRIVATE_KEY)
    if "Solana" in config.ENABLED_CHAINS:
        clients["Solana"] = SolanaClient(config.CHAIN_RPC["Solana"], config.PRIVATE_KEY)
    if "Base" in config.ENABLED_CHAINS:
        clients["Base"] = EvmClient(config.CHAIN_RPC["Base"], chain_id=8453, private_key=config.PRIVATE_KEY)
    if "BNB" in config.ENABLED_CHAINS:
        clients["BNB"] = EvmClient(config.CHAIN_RPC["BNB"], chain_id=56, private_key=config.PRIVATE_KEY)
    return clients


async def scan_once(
    session: aiohttp.ClientSession,
    chain_clients: dict[str, Any],
    strategies: list[Any],
    notifier: TelegramNotifier,
    ledger: TradeLedger,
) -> None:
    if session.closed:
        return

    log.info("=" * 60)
    log.info("Scanning at %s", time.strftime("%H:%M:%S"))

    candidates: list[Opportunity] = []
    for chain in config.ENABLED_CHAINS:
        chain_client = chain_clients.get(chain)
        if chain_client is None:
            log.warning("No client registered for chain %s, skipping", chain)
            continue
        dexes = CHAIN_DEXES.get(chain, [])
        base_token = config.CHAIN_BASE_TOKEN.get(chain, "")

        for strat in strategies:
            try:
                opps = await strat.find(session, chain, chain_client, dexes, base_token)
            except Exception as e:  # noqa: BLE001
                log.exception("Strategy %s failed on %s: %s", strat.name, chain, e)
                continue
            candidates.extend(opps)

    if not candidates:
        log.info("No opportunities found this cycle.")
        return

    best = max(candidates, key=lambda o: o.profit_pct)
    log.info("Best: %s on %s — %.2f%% net", best.label, best.chain, best.profit_pct)

    report = await run_safety_checks(best, chain_clients[best.chain], session)
    if not report.passed:
        log.info("Safety check FAILED: %s", " | ".join(report.reasons))
        return

    log.info("=" * 60)
    log.info("OPPORTUNITY (safety passed)")
    log.info("  %s", best.label)
    log.info("  profit: %.2f%%  liquidity: $%.0f  slip: %.2f%%",
             best.profit_pct, best.liquidity_usd, best.estimated_slippage_pct)
    log.info("=" * 60)

    if not config.AUTO_TRADE:
        log.info("[DRY RUN] AUTO_TRADE=false — not executing")
        return

    try:
        result = await chain_clients[best.chain].execute_route(best)
    except NotImplementedError as e:
        log.error("AUTO_TRADE requested but execute_route is unimplemented: %s", e)
        await notifier.critical_error(f"execute_route not implemented on {best.chain}")
        return
    except Exception as e:  # noqa: BLE001
        log.exception("Trade execution failed: %s", e)
        await notifier.trade_failed(best, str(e))
        return

    record = TradeRecord(
        chain=best.chain,
        dex=best.legs[0]["dex"] if best.legs else "n/a",
        side="route",
        amount_in=getattr(result, "amount_in", config.TRADE_SIZE),
        amount_out=getattr(result, "amount_out", config.TRADE_SIZE),
        profit_pct=best.profit_pct,
        timestamp=time.time(),
    )
    ledger.record(record)
    await notifier.trade_executed(record)
    await notifier.profit(record)


async def run() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if config.LOG_FILE:
        handlers.append(logging.FileHandler(config.LOG_FILE))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )

    dex_names = [d.NAME for d in TON_DEXES]
    config.validate(dex_names)

    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    chain_clients = _build_chain_clients()
    strategies = build_strategies(config.STRATEGIES)
    ledger = TradeLedger()

    log.info(
        "Starting: chains=%s strategies=%s interval=%ds auto_trade=%s telegram=%s",
        config.ENABLED_CHAINS, config.STRATEGIES, config.SCAN_INTERVAL,
        config.AUTO_TRADE, notifier._enabled,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    # ONE session for the entire run. The previous version could lose it on
    # shutdown; this version guarantees: stop -> drain -> close.
    timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_read=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        notifier.set_session(session)

        try:
            while not stop.is_set():
                # Use a task group so we can wait for the scan to finish and
                # cancel cleanly on shutdown, with no stragglers hitting a
                # half-closed session.
                scan_task = asyncio.create_task(
                    scan_once(session, chain_clients, strategies, notifier, ledger)
                )
                done_event = asyncio.Event()

                def _on_scan_done(t: asyncio.Task) -> None:
                    # Surface unhandled exceptions loudly so we don't crash
                    # silently on something like a closed session.
                    try:
                        t.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:  # noqa: BLE001
                        log.exception("scan_once raised: %s", e)
                    loop.call_soon_threadsafe(done_event.set)

                scan_task.add_done_callback(_on_scan_done)

                sleep_task = asyncio.create_task(stop.wait())
                done, _pending = await asyncio.wait(
                    {scan_task, sleep_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if sleep_task in done and not scan_task.done():
                    # Shutdown requested mid-scan: cancel the scan, await it
                    # so all background fetches are torn down, THEN exit the
                    # `async with` (which closes the session cleanly).
                    scan_task.cancel()
                    try:
                        await scan_task
                    except (asyncio.CancelledError, Exception):
                        pass
                else:
                    sleep_task.cancel()

                if not stop.is_set():
                    # No shutdown signal — wait the configured interval, but
                    # bail out immediately if a signal arrives.
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=config.SCAN_INTERVAL)
                    except asyncio.TimeoutError:
                        pass

                if ledger.new_day() and not stop.is_set():
                    try:
                        await notifier.daily_summary(ledger)
                    except Exception as e:  # noqa: BLE001
                        log.warning("daily_summary failed: %s", e)
        finally:
            # Final summary AFTER any in-flight tasks have settled, BEFORE
            # the `async with` closes the session.
            try:
                await notifier.daily_summary(ledger)
            except Exception as e:  # noqa: BLE001
                log.warning("final daily_summary failed: %s", e)

    log.info("Scanner stopped cleanly.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()

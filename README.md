# Surf Arbitrage Scanner — Multi-chain, Multi-strategy

Lightweight, single-command scanner and trader for **GRAM** (and other base
tokens) across multiple DEXes and chains. Pure Python. No Docker, no
database, no web server, no dashboard.

## Quick start (Termux)

```bash
pkg update && pkg install python
pip install -r requirements.txt
cp .env.example .env       # then edit .env
python main.py

## Run in the background (Termux)
termux-wake-lock
nohup python main.py >> ~/scan.log 2>&1 &
echo $! > ~/scan.pid

# to stop:
kill "$(cat ~/scan.pid)"
termux-wake-unlock

## What it does
Multi-chain. Scans TON, Solana, Base, BNB (extensible — see below).
Multi-DEX. Each chain has its own DEX adapter list. New DEXes just need a module with NAME and async get_price(session).
Multi-strategy. Three pluggable strategies:
cross_dex — buy on DEX A, sell on DEX B
route — multi-hop round-trips within a DEX (needs get_all_pools on the DEX adapter)
new_pair — snipe new pools (needs get_recent_pools on the DEX adapter)
Safety checks. Every opportunity must pass all of: minimum profit, minimum liquidity, slippage, wallet balance, valid route, and successful simulation. Any failure → trade skipped, scanning continues.
Telegram notifications. Trade executed, trade failed, profit, daily summary, critical errors.
Print-only by default. Set AUTO_TRADE=true to attempt real execution. The on-chain execute_route is a placeholder — wire in your real wallet and DEX router code before enabling.

## Adding a new chain
Create <chain>.py with a client class exposing get_wallet_balance, simulate_route, execute_route (see solana.py / evm.py for the pattern).
Add the chain to _build_chain_clients() in main.py.
Add its DEX adapters to CHAIN_DEXES in main.py.
Add the chain name to VALID_CHAINS in config.py.

## Adding a new strategy
# in strategies.py
class MyStrategy:
    name = "my_strategy"
    async def find(self, session, chain, chain_client, dexes, base_token):
        # return list[Opportunity]
        return []

STRATEGY_REGISTRY["my_strategy"] = MyStrategy
Then add my_strategy to STRATEGIES in .env.

Adding a new DEX
# mydex.py
NAME = "MyDEX"
async def get_price(session):
    # return float | None
    ...
Register it in the appropriate *_DEXES list in main.py.

## Notes
The route and new_pair strategies are dormant until the relevant DEX adapters grow get_all_pools / get_recent_pools. The bot will run fine with just cross_dex.
DEX endpoints change field names occasionally — if prices go to None, check the official docs for the affected DEX.
Stop anytime with Ctrl+C. Signal handlers shut down cleanly.
Keep memory and CPU low: aiohttp + asyncio, no background workers, no thread pools, in-memory ledger only.

## Troubleshooting
Symptom	Cause
price unavailable everywhere	Network / DNS issue.
price unavailable on one DEX	That DEX's API shape changed — see the adapter's docstring.
safety check FAILED: balance ... < required ...	Wallet underfunded, or wallet_address not set on the chain client.
safety check FAILED: simulation not implemented	Set AUTO_TRADE=false until you wire in real simulation.
AUTO_TRADE is enabled but PRIVATE_KEY is empty	Set PRIVATE_KEY in .env.
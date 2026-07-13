"""
Loads bot configuration from .env. Pure parsing — no network. Validates types
and ranges at import time so misconfiguration fails fast and loud rather than
silently degrading behaviour at scan time.
"""

from __future__ import annotations

import os
import sys
from typing import Final

from dotenv import load_dotenv

load_dotenv()


def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        print(f"[config] WARNING: {key}={val!r} is not a number, using default {default}")
        return default


def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        print(f"[config] WARNING: {key}={val!r} is not an integer, using default {default}")
        return default


def _get_str(key: str, default: str) -> str:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip()


def _get_list(key: str, default: list[str]) -> list[str]:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Wallet / chain ---
PRIVATE_KEY: Final[str] = _get_str("PRIVATE_KEY", "")
RPC: Final[str] = _get_str("RPC", "https://toncenter.com/api/v2/jsonRPC")

# --- Trading ---
TRADE_SIZE: Final[float] = _get_float("TRADE_SIZE", 10.0)
MIN_PROFIT: Final[float] = _get_float("MIN_PROFIT", 1.5)
SLIPPAGE: Final[float] = _get_float("SLIPPAGE", 0.5)
AUTO_TRADE: Final[bool] = _get_bool("AUTO_TRADE", False)

# --- Position management (0 disables) ---
TAKE_PROFIT: Final[float] = _get_float("TAKE_PROFIT", 0.0)
STOP_LOSS: Final[float] = _get_float("STOP_LOSS", 0.0)

# --- Liquidity floor (USD) ---
MIN_LIQUIDITY_USD: Final[float] = _get_float("MIN_LIQUIDITY_USD", 1000.0)

# --- Loop timing ---
SCAN_INTERVAL: Final[int] = _get_int("SCAN_INTERVAL", 5)

# --- Per-DEX fees (%) ---
DEX_FEES: Final[dict[str, float]] = {
    "STON.fi": 0.3,
    "DeDust": 0.3,
}

# --- Strategies ---
# Comma-separated. Available: cross_dex, route, new_pair.
STRATEGIES: Final[list[str]] = _get_list("STRATEGIES", ["cross_dex", "route", "new_pair"])
VALID_STRATEGIES: Final[set[str]] = {"cross_dex", "route", "new_pair"}

# --- Multi-chain ---
ENABLED_CHAINS: Final[list[str]] = _get_list("ENABLED_CHAINS", ["TON"])
VALID_CHAINS: Final[set[str]] = {"TON", "Solana", "Base", "BNB"}

# Per-chain base token. Every trade starts and ends in this token.
CHAIN_BASE_TOKEN: Final[dict[str, str]] = {
    "TON": _get_str("CHAIN_BASE_TOKEN_TON", "GRAM"),
    "Solana": _get_str("CHAIN_BASE_TOKEN_SOLANA", "SOL"),
    "Base": _get_str("CHAIN_BASE_TOKEN_BASE", "ETH"),
    "BNB": _get_str("CHAIN_BASE_TOKEN_BNB", "BNB"),
}

# --- Intermediary jettons to scan for cross-DEX arbitrage ---
# Every trade: base_token -> jetton -> base_token.
# Add any TON jetton symbol (e.g. USDT, USDC, NOT, jUSDT, HMSTR) you want
# the bot to consider as a path. GRAM/USDT remains the default for any
# jetton list that doesn't include USDT.
INTERMEDIATE_JETTONS: Final[list[str]] = _get_list(
    "INTERMEDIATE_JETTONS", ["USDT", "DOGS", "DUST", "NOT", "PX"]
)

# Per-chain RPC endpoints.
CHAIN_RPC: Final[dict[str, str]] = {
    "TON": _get_str("CHAIN_RPC_TON", RPC),
    "Solana": _get_str("CHAIN_RPC_SOLANA", "https://api.mainnet-beta.solana.com"),
    "Base": _get_str("CHAIN_RPC_BASE", "https://mainnet.base.org"),
    "BNB": _get_str("CHAIN_RPC_BNB", "https://bsc-dataseed.binance.org"),
}

# --- Telegram ---
TELEGRAM_BOT_TOKEN: Final[str] = _get_str("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: Final[str] = _get_str("TELEGRAM_CHAT_ID", "")

# --- Logging ---
LOG_FILE: Final[str] = _get_str("LOG_FILE", "")  # empty = stdout only


def validate(dex_names: list[str]) -> None:
    """Hard-fail on bad config. Soft-warn on missing-but-tolerable values."""
    errors: list[str] = []

    if not RPC.startswith(("http://", "https://")):
        errors.append(f"RPC must be an http(s) URL, got {RPC!r}")
    if TRADE_SIZE <= 0:
        errors.append(f"TRADE_SIZE must be > 0, got {TRADE_SIZE}")
    if MIN_PROFIT < 0:
        errors.append(f"MIN_PROFIT must be >= 0, got {MIN_PROFIT}")
    if SLIPPAGE < 0:
        errors.append(f"SLIPPAGE must be >= 0, got {SLIPPAGE}")
    if TAKE_PROFIT < 0:
        errors.append(f"TAKE_PROFIT must be >= 0, got {TAKE_PROFIT}")
    if STOP_LOSS < 0:
        errors.append(f"STOP_LOSS must be >= 0, got {STOP_LOSS}")
    if MIN_LIQUIDITY_USD < 0:
        errors.append(f"MIN_LIQUIDITY_USD must be >= 0, got {MIN_LIQUIDITY_USD}")
    if SCAN_INTERVAL < 1:
        errors.append(f"SCAN_INTERVAL must be >= 1 second, got {SCAN_INTERVAL}")
    if AUTO_TRADE and not PRIVATE_KEY:
        errors.append("AUTO_TRADE=true requires PRIVATE_KEY to be set")
    if not ENABLED_CHAINS:
        errors.append("ENABLED_CHAINS is empty — nothing to scan")
    for c in ENABLED_CHAINS:
        if c not in VALID_CHAINS:
            errors.append(f"ENABLED_CHAINS contains unknown chain {c!r}")
        if c in CHAIN_RPC and not CHAIN_RPC[c].startswith(("http://", "https://")):
            errors.append(f"CHAIN_RPC[{c!r}] must be an http(s) URL")
    if not STRATEGIES:
        errors.append("STRATEGIES is empty — nothing to run")
    for s in STRATEGIES:
        if s not in VALID_STRATEGIES:
            errors.append(f"STRATEGIES contains unknown strategy {s!r}")
    for name in dex_names:
        if name not in DEX_FEES:
            print(f"[config] WARNING: no DEX_FEES entry for {name!r}, will use 0%")
    for name, fee in DEX_FEES.items():
        if fee < 0:
            errors.append(f"DEX_FEES[{name!r}] must be >= 0, got {fee}")

    if errors:
        for e in errors:
            print(f"[config] ERROR: {e}")
        sys.exit(1)

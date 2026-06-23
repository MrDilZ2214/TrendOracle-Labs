"""
core/price_cache.py — Real-Time Bitget Price Cache
===================================================
Extracted from main.py so ws_dispatcher.py can import without
creating a circular dependency.
"""
import threading
import time
import requests
from datetime import datetime

import sys as _sys, os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
import config as _cfg
BITGET_BASE = _cfg.BITGET_BASE

_price_cache: dict = {}
_market_cache: dict = {}
_price_cache_lock = threading.Lock()

TRACKED_SYMBOLS: list = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "BNBUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT",
    "DOGEUSDT", "LTCUSDT", "LINKUSDT", "MATICUSDT",
    "ATOMUSDT", "UNIUSDT", "NEARUSDT", "FTMUSDT",
    "SHIBUSDT", "TRXUSDT", "XLMUSDT", "VETUSDT",
]
_tracked_symbols_lock = threading.Lock()


def add_tracked_symbol(symbol: str):
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    with _tracked_symbols_lock:
        if sym not in TRACKED_SYMBOLS:
            TRACKED_SYMBOLS.append(sym)
            print(f"[PriceCache] Now tracking: {sym}")


def _bitget_fetch_tickers(symbols: list | None = None) -> list:
    try:
        params = {}
        if symbols and len(symbols) == 1:
            params["symbol"] = symbols[0]
        r = requests.get(f"{BITGET_BASE}/tickers", params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if symbols and len(symbols) > 1:
            sym_set = set(symbols)
            data = [d for d in data if d.get("symbol") in sym_set]
        return data
    except Exception as e:
        print(f"[Bitget] Ticker fetch error: {e}")
        return []


def _refresh_price_cache():
    while True:
        try:
            with _tracked_symbols_lock:
                syms_to_fetch = list(TRACKED_SYMBOLS)
            tickers = _bitget_fetch_tickers(syms_to_fetch)
            with _price_cache_lock:
                for t in tickers:
                    sym = t.get("symbol", "")
                    _price_cache[sym] = {
                        "price":     float(t.get("lastPr", 0)),
                        "change24h": round(float(t.get("change24h", 0)) * 100, 2),
                        "high24h":   float(t.get("high24h", 0)),
                        "low24h":    float(t.get("low24h", 0)),
                        "volume":    float(t.get("usdtVolume", 0)),
                        "ts":        datetime.now().isoformat(),
                    }
        except Exception as e:
            print(f"[PriceCache] Error: {e}")
        time.sleep(5)


def _refresh_market_cache():
    while True:
        try:
            all_tickers = _bitget_fetch_tickers()
            usdt = [t for t in all_tickers if t.get("symbol", "").endswith("USDT")]

            gainers = sorted(usdt, key=lambda x: float(x.get("change24h", 0)), reverse=True)[:10]
            top_gainers = []
            for g in gainers:
                top_gainers.append({
                    "symbol": g["symbol"].replace("USDT", ""),
                    "price":  float(g.get("lastPr", 0)),
                    "change": round(float(g.get("change24h", 0)) * 100, 2),
                    "volume": float(g.get("usdtVolume", 0)),
                })

            losers = sorted(usdt, key=lambda x: float(x.get("change24h", 0)))[:5]
            top_losers = []
            for l in losers:
                top_losers.append({
                    "symbol": l["symbol"].replace("USDT", ""),
                    "price":  float(l.get("lastPr", 0)),
                    "change": round(float(l.get("change24h", 0)) * 100, 2),
                })

            total_vol = sum(float(t.get("usdtVolume", 0)) for t in usdt)

            with _price_cache_lock:
                _market_cache["top_gainers"]  = top_gainers
                _market_cache["top_losers"]   = top_losers
                _market_cache["total_volume"] = total_vol
                _market_cache["ts"]           = datetime.now().isoformat()
                _market_cache["pairs_count"]  = len(usdt)

        except Exception as e:
            print(f"[MarketCache] Error: {e}")
        time.sleep(30)


def get_fresh_price(symbol: str) -> dict:
    """
    Always hits Bitget directly for the *current* price instead of
    returning a value that may be up to 5s stale from the background
    cache. Used for actions where the exact real entry price matters
    (e.g. opening a demo/real trade) so each trade gets its own true
    entry price instead of multiple trades reusing one cached tick.
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    tickers = _bitget_fetch_tickers([sym])
    if tickers:
        t = tickers[0]
        result = {
            "price":     float(t.get("lastPr", 0)),
            "change24h": round(float(t.get("change24h", 0)) * 100, 2),
            "volume":    float(t.get("usdtVolume", 0)),
            "ts":        datetime.now().isoformat(),
        }
        with _price_cache_lock:
            _price_cache[sym] = result
        return result
    # Fall back to whatever is cached if the live fetch fails
    with _price_cache_lock:
        return _price_cache.get(sym, {})


def get_cached_price(symbol: str) -> dict:
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    with _price_cache_lock:
        if sym in _price_cache:
            return _price_cache[sym]
    tickers = _bitget_fetch_tickers([sym])
    if tickers:
        t = tickers[0]
        result = {
            "price":     float(t.get("lastPr", 0)),
            "change24h": round(float(t.get("change24h", 0)) * 100, 2),
            "volume":    float(t.get("usdtVolume", 0)),
            "ts":        datetime.now().isoformat(),
        }
        with _price_cache_lock:
            _price_cache[sym] = result
        return result
    return {}

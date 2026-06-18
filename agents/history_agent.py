"""
agents/history_agent.py — Price History Agent
=============================================
Fetches OHLCV candle history from Bitget for a given symbol.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "tools"), os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import requests
from datetime import datetime, timedelta


_GRANULARITY_MAP = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1D": "1day", "1d": "1day", "3D": "3day", "3d": "3day",
    "1W": "1week", "1w": "1week", "1M": "1month",
}


def get_candle_history(symbol: str, interval: str = "1day", limit: int = 30) -> list:
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    gran = _GRANULARITY_MAP.get(interval, interval)

    BITGET_KLINE_URL = "https://api.bitget.com/api/v2/spot/market/candles"
    try:
        params = {
            "symbol":      sym,
            "granularity": gran,
            "limit":       limit,
        }
        r = requests.get(BITGET_KLINE_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        candles = []
        for c in data:
            ts    = int(c[0]) // 1000
            open_ = float(c[1])
            high  = float(c[2])
            low   = float(c[3])
            close = float(c[4])
            vol   = float(c[5])
            candles.append({
                "date":   datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                "open":   open_,
                "high":   high,
                "low":    low,
                "close":  close,
                "volume": vol,
            })
        return candles
    except Exception as e:
        print(f"[HistoryAgent] Error fetching {sym}: {e}")
        return []


def get_price_history(symbol: str, days: int = 30) -> list:
    return get_candle_history(symbol, interval="1day", limit=days)


if __name__ == "__main__":
    import json
    history = get_price_history("BTC", 7)
    print(json.dumps(history, indent=2))

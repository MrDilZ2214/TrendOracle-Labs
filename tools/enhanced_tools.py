"""
tools/enhanced_tools.py — Bitget Market Data Tools
====================================================
Provides live price and top gainers from Bitget v2 API.
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import requests
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INTERVAL_MAP = {
    "1m": "1min", "3m": "5min", "5m": "5min",
    "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "1h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1day", "3d": "3day", "1w": "1week", "1M": "1M",
}


class EnhancedTools:
    def __init__(self):
        self.base_url = "https://api.bitget.com/api/v2/spot/market"
        self.session  = self._get_session()

    def _get_session(self):
        session = requests.Session()
        retry   = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://",  adapter)
        session.mount("https://", adapter)
        return session

    def get_live_price(self, symbol):
        if not symbol.endswith("USDT") and "USDT" not in symbol:
            symbol = f"{symbol.upper()}USDT"
        else:
            symbol = symbol.upper()

        try:
            response = self.session.get(
                f"{self.base_url}/tickers",
                params={"symbol": symbol},
                timeout=10
            )
            response.raise_for_status()
            data   = response.json()
            ticker = data["data"][0]
            return {
                "symbol":    ticker["symbol"],
                "price":     float(ticker["lastPr"]),
                "timestamp": time.time()
            }
        except Exception as e:
            return {"error": f"Failed to fetch price for {symbol}: {str(e)}"}

    def get_top_gainers(self, limit=5):
        try:
            response = self.session.get(f"{self.base_url}/tickers", timeout=15)
            response.raise_for_status()
            tickers      = response.json()["data"]
            usdt_tickers = [t for t in tickers if t['symbol'].endswith('USDT')]
            sorted_tickers = sorted(
                usdt_tickers,
                key=lambda x: float(x.get('change24h', 0)),
                reverse=True
            )
            top_gainers = []
            for t in sorted_tickers[:limit]:
                top_gainers.append({
                    "symbol":         t['symbol'],
                    "price":          float(t['lastPr']),
                    "change_percent": round(float(t.get('change24h', 0)) * 100, 2),
                    "volume":         float(t.get('usdtVolume', 0))
                })
            return top_gainers
        except Exception as e:
            return {"error": f"Failed to fetch top gainers: {str(e)}"}


if __name__ == "__main__":
    et = EnhancedTools()
    print("Testing Live Price (BTC):", et.get_live_price("BTC"))
    print("Testing Top Gainers:", et.get_top_gainers(3))

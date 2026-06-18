"""
core/memory_manager.py — AI Memory Manager
==========================================
Stores agent memory in memory.json at the project root.
"""
import json
import os
import threading
from datetime import datetime, timedelta

ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_FILE = os.path.join(ROOT_DIR, "memory.json")

_LOCK = threading.Lock()


class MemoryManager:
    def __init__(self, file_path=MEMORY_FILE):
        self.file_path = file_path

    @property
    def data(self):
        return self._load_memory()

    def _load_memory(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading memory: {e}")

        return {
            "last_updated":      "",
            "market_summary":    {},
            "news_analysis":     [],
            "asset_news":        {},
            "technical_signals": {},
            "whale_alerts":      [],
            "point_history":     {}
        }

    def _save(self, data):
        data["last_updated"] = datetime.now().isoformat()

        def json_serial(obj):
            import pandas as pd
            import numpy as np
            if isinstance(obj, (datetime, pd.Timestamp)):
                return obj.isoformat()
            if isinstance(obj, pd.Series):
                return obj.to_dict()
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            raise TypeError(f"Type {type(obj)} not serializable")

        with _LOCK:
            try:
                with open(self.file_path, 'w') as f:
                    json.dump(data, f, indent=4, default=json_serial)
            except Exception as e:
                print(f"Error saving memory: {e}")

    def update_market_summary(self, summary):
        d = self._load_memory()
        d["market_summary"] = summary
        self._save(d)

    def add_news(self, news_item, symbol=None):
        d = self._load_memory()
        if symbol:
            symbol = symbol.upper()
            if "asset_news" not in d:
                d["asset_news"] = {}
            if symbol not in d["asset_news"]:
                d["asset_news"][symbol] = []
            d["asset_news"][symbol].append(news_item)
            d["asset_news"][symbol] = d["asset_news"][symbol][-20:]
        else:
            if "news_analysis" not in d:
                d["news_analysis"] = []
            d["news_analysis"].append(news_item)
            d["news_analysis"] = d["news_analysis"][-50:]
        self._save(d)

    def update_technical(self, symbol, signals):
        d = self._load_memory()
        if "technical_signals" not in d:
            d["technical_signals"] = {}
        d["technical_signals"][symbol] = signals
        self._save(d)

    def add_whale_alert(self, alert):
        d = self._load_memory()
        if "whale_alerts" not in d:
            d["whale_alerts"] = []
        d["whale_alerts"].append(alert)
        d["whale_alerts"] = d["whale_alerts"][-20:]
        self._save(d)

    def update_points(self, symbol, score, reason):
        d = self._load_memory()
        today  = datetime.now().strftime("%Y-%m-%d")
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if "point_history" not in d:
            d["point_history"] = {}
        if symbol not in d["point_history"]:
            d["point_history"][symbol] = []
        d["point_history"][symbol].append({
            "date":      today,
            "score":     score,
            "reason":    reason,
            "timestamp": datetime.now().isoformat()
        })
        d["point_history"][symbol] = [
            e for e in d["point_history"][symbol] if e["date"] >= cutoff
        ]
        self._save(d)

    def get_memory_summary(self):
        d = self._load_memory()
        return {
            "market":              d.get("market_summary", {}),
            "latest_news":         d.get("news_analysis", [])[-5:],
            "tech_signals":        d.get("technical_signals", {}),
            "recent_whale_alerts": d.get("whale_alerts", [])[-5:],
            "points":              {s: h[-1] if h else None
                                    for s, h in d.get("point_history", {}).items()}
        }

    def __getitem__(self, key):
        return self._load_memory()[key]


if __name__ == "__main__":
    mm = MemoryManager()
    print("Memory Manager initialized.")
    print("Summary:", mm.get_memory_summary())

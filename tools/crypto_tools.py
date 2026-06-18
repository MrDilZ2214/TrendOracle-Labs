#!/usr/bin/env python3
"""
tools/crypto_tools.py — Unified Tool Interface for MainAgent (SQLite-backed)
=============================================================================
Reads from SQLite kv_store (news, technical, whale, market_summary)
and delegates to sub-agents as needed.
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "agents"), os.path.join(ROOT_DIR, "tools"), os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import json
from datetime import datetime

import database
from history_agent import get_price_history
from enhanced_tools import EnhancedTools


# ══════════════════════════════════════════════════════════════════
#  DB Helpers (SQLite-backed)
# ══════════════════════════════════════════════════════════════════

def load_technical_db() -> dict:
    db = database.kv_get("technical")
    if not db:
        return {"last_updated": "", "total_points": {"up": 0, "down": 0, "total": 0}, "coin_points": {}, "snapshots": {}}
    return db


def save_technical_db(db: dict):
    database.kv_set("technical", db)


def load_whale_db() -> dict:
    db = database.kv_get("whale")
    if not db:
        return {
            "last_updated": "",
            "danger_level": {"level": "UNKNOWN", "score": 0, "label": "NO DATA", "reasons": []},
            "total_points": {"up": 5, "down": 5, "total": 10},
            "market_stats": {},
            "alerts":       [],
            "hodl_stats":   {}
        }
    return db


def load_market_summary_db() -> dict:
    db = database.kv_get("market_summary")
    if not db:
        return {
            "last_updated":       "",
            "master_points":      {"up": 0, "down": 0, "total": 0, "sentiment": "NEUTRAL", "sentiment_pct": 50.0},
            "coin_master_points": {},
            "live_prices":        {},
            "top_gainers":        [],
            "danger_level":       {"level": "UNKNOWN", "score": 0, "label": "NO DATA", "reasons": []},
            "ai_summary":         {"text": "No summary available yet.", "action": "WAIT", "key_points": []}
        }
    return db


# ══════════════════════════════════════════════════════════════════
#  Technical live-fetch fallback
# ══════════════════════════════════════════════════════════════════

def _calculate_up_down_score(signals_result: dict):
    buy_count  = signals_result.get("buy_count", 0)
    sell_count = signals_result.get("sell_count", 0)
    neut_count = signals_result.get("neut_count", 0)
    total      = buy_count + sell_count + neut_count
    if total == 0:
        return 5, 5
    raw = (buy_count / total) * 10
    up  = max(1, min(9, round(raw)))
    return up, 10 - up


def _update_points_on_snapshot(db: dict, coin: str, up_score: int, down_score: int):
    coin = coin.upper()
    old  = db["snapshots"].get(coin, {})
    old_up   = old.get("up_score", 0)
    old_down = old.get("down_score", 0)

    if coin not in db["coin_points"]:
        db["coin_points"][coin] = {"up": 0, "down": 0, "total": 0}

    db["coin_points"][coin]["up"]    = db["coin_points"][coin]["up"]    - old_up   + up_score
    db["coin_points"][coin]["down"]  = db["coin_points"][coin]["down"]  - old_down + down_score
    db["coin_points"][coin]["total"] = 10
    db["total_points"]["up"]    = db["total_points"]["up"]    - old_up   + up_score
    db["total_points"]["down"]  = db["total_points"]["down"]  - old_down + down_score
    db["total_points"]["total"] = len(db["coin_points"]) * 10


def _live_fetch_snapshot(symbol_usdt: str) -> dict:
    try:
        import crypto_technical_analysis as cta
        df = cta.fetch_ohlcv(symbol_usdt)
        if df is None:
            return None
        price  = cta.fetch_price(symbol_usdt) or float(df.iloc[-1]['close'])
        engine = cta.SignalEngine(df)
        result = engine.get_signals()
        up, down = _calculate_up_down_score(result)
        coin = symbol_usdt.replace("USDT", "")

        clean_signals = {}
        for name, data in result.get("signals", {}).items():
            clean_signals[name] = {
                "signal":  data.get("signal", "?"),
                "details": data.get("details", "")
            }

        buy_count  = result.get("buy_count", 0)
        sell_count = result.get("sell_count", 0)
        neut_count = result.get("neut_count", 0)
        total_ind  = buy_count + sell_count + neut_count

        return {
            "symbol":           coin,
            "timestamp":        datetime.now().isoformat(),
            "price":            round(float(price), 4),
            "consensus":        result.get("consensus", "NEUTRAL"),
            "up_score":         up,
            "down_score":       down,
            "buy_count":        buy_count,
            "sell_count":       sell_count,
            "neutral_count":    neut_count,
            "total_indicators": total_ind,
            "pct_buy":          round(result.get("pct_buy", 50.0), 1),
            "pct_sell":         round(result.get("pct_sell", 50.0), 1),
            "tp":               round(result["tp"], 4) if result.get("tp") else None,
            "sl":               round(result["sl"], 4) if result.get("sl") else None,
            "rr":               round(result["rr"], 2) if result.get("rr") else None,
            "atr":              round(float(result.get("atr", 0)), 4),
            "signals":          clean_signals
        }
    except Exception as e:
        print(f"[Tools] Live fetch error for {symbol_usdt}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  CryptoTools class
# ══════════════════════════════════════════════════════════════════

class CryptoTools:
    def __init__(self):
        self.enhanced_tools = EnhancedTools()

    def get_crypto_history(self, symbol: str) -> dict:
        result = get_price_history(symbol, 30)
        if isinstance(result, list) and len(result) > 0:
            return {
                "symbol":       symbol.upper(),
                "data_points":  len(result),
                "latest_price": result[-1].get("close") if result else None,
                "history":      result[-10:]
            }
        return {"symbol": symbol, "history": result}

    def get_latest_news(self, symbol=None, limit=5) -> dict:
        import crypto_news_reader
        db        = crypto_news_reader.load_news_db()
        news_list = db.get("news", [])

        if symbol:
            symbol   = symbol.upper().replace("USDT", "")
            filtered = [n for n in news_list if symbol in n.get("affected_coins", [])]
            if not filtered:
                filtered = [n for n in news_list if "BTC" in n.get("affected_coins", [])]
            news_list = filtered

        news_list = sorted(news_list, key=lambda x: x.get("timestamp", ""), reverse=True)
        news_list = news_list[:limit]

        result = {
            "total_points": db.get("total_points", {}),
            "news_count":   len(news_list),
            "news":         news_list
        }

        if symbol and symbol in db.get("coin_points", {}):
            result["coin_points"] = {symbol: db["coin_points"][symbol]}

        return result

    def fetch_and_analyze_asset_news(self, symbol: str) -> dict:
        import crypto_news_reader
        symbol = symbol.upper()
        print(f"Searching specific news for {symbol}...")

        all_articles = []
        for name in ["CoinDesk", "Cointelegraph", "CryptoSlate"]:
            articles = crypto_news_reader.get_latest_news_feed(name, crypto_news_reader.FEEDS[name])
            all_articles.extend(articles)

        relevant = []
        for a in all_articles:
            if symbol.lower() in a.title.lower() or (
                hasattr(a, 'description') and a.description and symbol.lower() in a.description.lower()
            ):
                relevant.append(a)

        if not relevant:
            return {"status": f"No recent news articles found for {symbol}."}

        article = relevant[0]
        text = crypto_news_reader.get_article_text(article.link)
        if not text:
            text = getattr(article, "description", "No content")

        news_item = crypto_news_reader.analyze_and_save_news(
            title=article.title,
            content=text,
            link=article.link,
            source=article.source
        )

        if news_item:
            return {
                "status":   "success",
                "symbol":   symbol,
                "analysis": news_item,
                "title":    article.title,
                "link":     article.link
            }
        return {"status": f"Failed to analyze or save news for {symbol}.", "analysis": None}

    def get_technical_analysis(self, symbol: str) -> dict:
        coin     = symbol.upper().replace("USDT", "")
        db       = load_technical_db()
        snapshot = db["snapshots"].get(coin)

        if snapshot:
            try:
                ts          = datetime.fromisoformat(snapshot["timestamp"])
                age_seconds = (datetime.now() - ts).total_seconds()
                if age_seconds > 120:
                    print(f"[Tools] {coin} snapshot stale ({age_seconds:.0f}s), fetching live...")
                    fresh = _live_fetch_snapshot(coin + "USDT")
                    if fresh:
                        up, down = fresh["up_score"], fresh["down_score"]
                        _update_points_on_snapshot(db, coin, up, down)
                        db["snapshots"][coin] = fresh
                        save_technical_db(db)
                        snapshot = fresh
            except:
                pass
        else:
            print(f"[Tools] No snapshot for {coin}, fetching live...")
            fresh = _live_fetch_snapshot(coin + "USDT")
            if fresh:
                up, down = fresh["up_score"], fresh["down_score"]
                _update_points_on_snapshot(db, coin, up, down)
                db["snapshots"][coin] = fresh
                save_technical_db(db)
                snapshot = fresh

        if not snapshot:
            return {"symbol": coin, "status": "error", "message": "Failed to fetch technical data"}

        return {
            "status":       "success",
            "symbol":       coin,
            "coin_points":  db["coin_points"].get(coin, {}),
            "total_points": db["total_points"],
            "snapshot":     snapshot
        }

    def get_all_technicals(self) -> dict:
        db      = load_technical_db()
        overview = []
        for coin, snapshot in db.get("snapshots", {}).items():
            overview.append({
                "symbol":     coin,
                "price":      snapshot.get("price"),
                "consensus":  snapshot.get("consensus"),
                "up_score":   snapshot.get("up_score"),
                "down_score": snapshot.get("down_score"),
                "buy_count":  snapshot.get("buy_count"),
                "sell_count": snapshot.get("sell_count"),
                "tp":         snapshot.get("tp"),
                "sl":         snapshot.get("sl"),
                "rr":         snapshot.get("rr"),
                "timestamp":  snapshot.get("timestamp")
            })
        return {
            "status":       "success",
            "total_points": db.get("total_points", {}),
            "coin_points":  db.get("coin_points", {}),
            "last_updated": db.get("last_updated", ""),
            "coins":        overview
        }

    def get_current_points(self, symbol: str) -> dict:
        coin        = symbol.upper().replace("USDT", "")
        db          = load_market_summary_db()
        coin_master = db.get("coin_master_points", {}).get(coin, {})

        if not coin_master:
            return {
                "symbol":        coin,
                "status":        "No master points data available yet.",
                "sentiment":     "NEUTRAL",
                "sentiment_pct": 50.0
            }

        return {
            "symbol":        coin,
            "status":        "success",
            "sentiment":     coin_master.get("sentiment", "NEUTRAL"),
            "sentiment_pct": coin_master.get("sentiment_pct", 50.0),
            "up":            coin_master.get("up", 0),
            "down":          coin_master.get("down", 0),
            "total":         coin_master.get("total", 0),
            "breakdown":     coin_master.get("breakdown", {})
        }

    def get_whale_data(self) -> dict:
        db = load_whale_db()

        freshness_warning = None
        if db.get("last_updated"):
            try:
                age = (datetime.now() - datetime.fromisoformat(db["last_updated"])).total_seconds()
                if age > 300:
                    freshness_warning = f"Data is {int(age/60)} minutes old"
            except:
                pass

        return {
            "status":            "success",
            "danger_level":      db["danger_level"],
            "total_points":      db["total_points"],
            "market_stats":      db.get("market_stats", {}),
            "recent_alerts":     db.get("alerts", [])[:5],
            "hodl_stats":        db.get("hodl_stats", {}),
            "last_updated":      db.get("last_updated", ""),
            "freshness_warning": freshness_warning
        }

    def get_market_summary(self) -> dict:
        db = load_market_summary_db()

        freshness_warning = None
        if db.get("last_updated"):
            try:
                age = (datetime.now() - datetime.fromisoformat(db["last_updated"])).total_seconds()
                if age > 600:
                    freshness_warning = f"Summary is {int(age/60)} minutes old — may be stale"
            except:
                pass

        return {
            "status":             "success",
            "last_updated":       db.get("last_updated"),
            "freshness_warning":  freshness_warning,
            "master_points":      db.get("master_points", {}),
            "coin_master_points": db.get("coin_master_points", {}),
            "live_prices":        db.get("live_prices", {}),
            "top_gainers":        db.get("top_gainers", [])[:3],
            "danger_level":       db.get("danger_level", {}),
            "ai_summary":         db.get("ai_summary", {})
        }

    def get_trade_setup(self, symbol: str, timeframe: str = "4h") -> dict:
        import trade_setup_agent
        result = trade_setup_agent.get_trade_setup(symbol, timeframe)

        if result.get("error") and not result.get("text"):
            return {
                "status":  "error",
                "message": result["error"],
                "symbol":  symbol.upper()
            }

        return {
            "status":    "success",
            "symbol":    result.get("_sym", symbol.upper()),
            "timeframe": result.get("_tf", timeframe.upper()),
            "setup":     result.get("text", "")
        }

    def get_live_price(self, symbol: str) -> dict:
        return self.enhanced_tools.get_live_price(symbol)

    def get_top_gainers(self, limit=5) -> list:
        return self.enhanced_tools.get_top_gainers(limit)

    def get_market_history_points(self) -> dict:
        return load_market_summary_db().get("coin_master_points", {})


if __name__ == "__main__":
    tools = CryptoTools()
    print("CryptoTools — SQLite-backed.")

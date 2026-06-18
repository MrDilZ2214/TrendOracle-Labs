#!/usr/bin/env python3
"""
agents/sub_agents_runner.py — Sub Agents Runner v2 (SQLite-backed)
===================================================================
Background loops:
  news_agent_loop()          — 60s  → SQLite kv_store (key: "news")
  technical_agent_loop()     — 30s  → SQLite kv_store (key: "technical")
  whale_agent_loop()         — 60s  → SQLite kv_store (key: "whale")
  market_summary_loop()      — 5min → SQLite kv_store (key: "market_summary")
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "agents"), os.path.join(ROOT_DIR, "tools"), os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import time
import threading
from datetime import datetime

import crypto_news_reader
import crypto_technical_analysis
import whale_alert_monitor
import market_summary_agent
import database
from enhanced_tools import EnhancedTools


# ══════════════════════════════════════════════════════════════════
#  Technical Agent helpers
# ══════════════════════════════════════════════════════════════════

def load_technical_db() -> dict:
    db = database.kv_get("technical")
    if not db:
        return {
            "last_updated": "",
            "total_points": {"up": 0, "down": 0, "total": 0},
            "coin_points":  {},
            "snapshots":    {}
        }
    return db


def save_technical_db(db: dict):
    database.kv_set("technical", db)


def calculate_up_down_score(signals_result: dict):
    buy_count  = signals_result.get("buy_count", 0)
    sell_count = signals_result.get("sell_count", 0)
    neut_count = signals_result.get("neut_count", 0)
    total      = buy_count + sell_count + neut_count

    if total == 0:
        return 5, 5

    raw        = (buy_count / total) * 10
    up_score   = max(1, min(9, round(raw)))
    down_score = 10 - up_score
    return up_score, down_score


def update_points_on_snapshot(db: dict, coin: str, up_score: int, down_score: int):
    coin = coin.upper()

    old_snapshot = db["snapshots"].get(coin, {})
    old_up   = old_snapshot.get("up_score", 0)
    old_down = old_snapshot.get("down_score", 0)

    if coin not in db["coin_points"]:
        db["coin_points"][coin] = {"up": 0, "down": 0, "total": 0}

    db["coin_points"][coin]["up"]    = db["coin_points"][coin]["up"]    - old_up   + up_score
    db["coin_points"][coin]["down"]  = db["coin_points"][coin]["down"]  - old_down + down_score
    db["coin_points"][coin]["total"] = 10

    db["total_points"]["up"]    = db["total_points"]["up"]    - old_up   + up_score
    db["total_points"]["down"]  = db["total_points"]["down"]  - old_down + down_score
    db["total_points"]["total"] = len(db["coin_points"]) * 10


def build_snapshot(coin: str, price: float, result: dict, up_score: int, down_score: int) -> dict:
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
        "up_score":         up_score,
        "down_score":       down_score,
        "buy_count":        buy_count,
        "sell_count":       sell_count,
        "neutral_count":    neut_count,
        "total_indicators": total_ind,
        "pct_buy":          round(result.get("pct_buy", 50.0), 1),
        "pct_sell":         round(result.get("pct_sell", 50.0), 1),
        "tp":               round(result["tp"], 4)  if result.get("tp")  else None,
        "sl":               round(result["sl"], 4)  if result.get("sl")  else None,
        "rr":               round(result["rr"], 2)  if result.get("rr")  else None,
        "atr":              round(float(result.get("atr", 0)), 4),
        "signals":          clean_signals
    }


# ══════════════════════════════════════════════════════════════════
#  NEWS AGENT LOOP  (60s)
# ══════════════════════════════════════════════════════════════════

def news_agent_loop():
    print("News Agent Started — 60s interval...")
    while True:
        try:
            processed = crypto_news_reader.load_processed_links()
            all_new   = []

            for name, url in crypto_news_reader.FEEDS.items():
                articles = crypto_news_reader.get_latest_news_feed(name, url)
                if articles:
                    new_articles = [a for a in articles if a.link not in processed]
                    all_new.extend(new_articles)

            if all_new:
                all_new.sort(key=lambda x: getattr(x, "published_parsed", 0) or 0)

                for article in all_new:
                    text = crypto_news_reader.get_article_text(article.link)
                    if not text:
                        text = getattr(article, "description", "No content")

                    result = crypto_news_reader.analyze_and_save_news(
                        title   = article.title,
                        content = text,
                        link    = article.link,
                        source  = article.source
                    )

                    if result:
                        crypto_news_reader.send_telegram_news(result)

                    crypto_news_reader.save_processed_link(article.link)
            else:
                print("[News Agent] No new articles.")

        except Exception as e:
            print(f"[News Agent] Error: {e}")

        time.sleep(60)


# ══════════════════════════════════════════════════════════════════
#  TECHNICAL AGENT LOOP  (30s)
# ══════════════════════════════════════════════════════════════════

def technical_agent_loop():
    print("Technical Agent Started — 30s interval...")
    while True:
        try:
            db = load_technical_db()

            for symbol in crypto_technical_analysis.SYMBOLS:
                try:
                    df = crypto_technical_analysis.fetch_ohlcv(symbol)
                    if df is None:
                        print(f"[Tech Agent] Failed to fetch {symbol}, skipping...")
                        continue

                    price = crypto_technical_analysis.fetch_price(symbol)
                    if not price:
                        price = float(df.iloc[-1]['close'])

                    engine = crypto_technical_analysis.SignalEngine(df)
                    result = engine.get_signals()

                    up_score, down_score = calculate_up_down_score(result)
                    coin = symbol.replace("USDT", "")

                    snapshot = build_snapshot(coin, price, result, up_score, down_score)
                    update_points_on_snapshot(db, coin, up_score, down_score)
                    db["snapshots"][coin] = snapshot

                    print(f"[Tech Agent] {coin}: {result.get('consensus','?')} | up={up_score} down={down_score} | ${price:,.4g}")

                except Exception as coin_e:
                    print(f"[Tech Agent] {symbol} error: {coin_e}")
                    continue

            db["last_updated"] = datetime.now().isoformat()
            save_technical_db(db)
            print(f"[Tech Agent] ✓ All snapshots saved → SQLite | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            print(f"[Tech Agent] Error: {e}")

        time.sleep(30)


# ══════════════════════════════════════════════════════════════════
#  WHALE AGENT LOOP  (60s)
# ══════════════════════════════════════════════════════════════════

def whale_agent_loop():
    print("Whale Agent Started — 60s interval...")
    while True:
        try:
            raw_data = whale_alert_monitor.fetch_data()

            if raw_data:
                danger     = whale_alert_monitor.calculate_danger_level(raw_data)
                up_score, down_score = whale_alert_monitor.calculate_up_down_from_danger(danger["score"])
                stats      = whale_alert_monitor.parse_market_stats(raw_data)

                alerts_parsed = []
                for a in raw_data.get('alerts', [])[:9]:
                    parsed = whale_alert_monitor.parse_alert_string(a)
                    parsed["id"]        = f"wh_{hash(str(a)) % 100000:05d}"
                    parsed["timestamp"] = datetime.now().isoformat()
                    parsed["raw"]       = str(a)
                    alerts_parsed.append(parsed)

                hodl_change = float(raw_data.get('hodl', {}).get('c', 0) or 0)

                db = {
                    "last_updated": datetime.now().isoformat(),
                    "danger_level": danger,
                    "total_points": {"up": up_score, "down": down_score, "total": 10},
                    "market_stats": stats,
                    "alerts":       alerts_parsed,
                    "hodl_stats": {
                        "hodl_change": hodl_change,
                        "comment":     whale_alert_monitor.hodl_comment(hodl_change)
                    }
                }

                whale_alert_monitor.save_whale_db(db)

                print(f"[Whale Agent] Danger: {danger['label']} ({danger['score']}/10) | up={up_score} down={down_score} | BTC ${stats['btc_price']:,.0f}")
                for r in danger.get("reasons", [])[:2]:
                    print(f"[Whale Agent]   → {r}")
                print(f"[Whale Agent] ✓ Saved → SQLite | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print("[Whale Agent] Failed to fetch whale data")

        except Exception as e:
            print(f"[Whale Agent] Error: {e}")

        time.sleep(60)


# ══════════════════════════════════════════════════════════════════
#  MARKET SUMMARY LOOP  (5 min)
# ══════════════════════════════════════════════════════════════════

def market_summary_loop():
    print("Market Summary Agent Started — 5min interval...")
    et = EnhancedTools()

    while True:
        try:
            master_pts      = market_summary_agent.calculate_master_points()
            coin_master_pts = market_summary_agent.calculate_coin_master_points()

            live_prices = {}
            for coin in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
                try:
                    p = et.get_live_price(coin)
                    if p.get("price"):
                        live_prices[coin] = {
                            "price":      float(str(p["price"]).replace(",", "")),
                            "change_24h": 0
                        }
                except:
                    pass

            try:
                top_gainers_raw = et.get_top_gainers(5)
                top_gainers = top_gainers_raw if isinstance(top_gainers_raw, list) else []

                for g in top_gainers:
                    sym = g.get("symbol", "").replace("USDT", "")
                    if sym and "change_percent" in g:
                        if sym in live_prices:
                            live_prices[sym]["change_24h"] = g["change_percent"]
            except:
                top_gainers = []

            whale_db     = database.kv_get("whale")
            danger_level = whale_db.get("danger_level", {"level": "UNKNOWN", "score": 0, "label": "NO DATA"})

            news_db      = database.kv_get("news")
            latest_news  = news_db.get("news", [])[-5:]
            whale_alerts = whale_db.get("alerts", [])[:3]

            context = {
                "master_points":      master_pts,
                "coin_master_points": coin_master_pts,
                "live_prices":        live_prices,
                "top_gainers":        top_gainers,
                "danger_level":       danger_level,
                "latest_news":        latest_news,
                "whale_alerts":       whale_alerts
            }
            ai_summary = market_summary_agent.generate_ai_summary(context)

            db = {
                "last_updated":       datetime.now().isoformat(),
                "master_points":      master_pts,
                "coin_master_points": coin_master_pts,
                "live_prices":        live_prices,
                "top_gainers":        top_gainers,
                "danger_level":       danger_level,
                "ai_summary":         ai_summary
            }
            market_summary_agent.save_market_summary_db(db)

            breakdown = master_pts.get("breakdown", {})
            news_pct  = round(breakdown.get("news", {}).get("up", 0) / max(breakdown.get("news", {}).get("total", 1), 1) * 100, 1)
            tech_pct  = round(breakdown.get("technical", {}).get("up", 0) / max(breakdown.get("technical", {}).get("total", 1), 1) * 100, 1)
            whale_pct = round(breakdown.get("whale", {}).get("up", 0) / max(breakdown.get("whale", {}).get("total", 1), 1) * 100, 1)

            print(f"[Market Summary] {master_pts['sentiment']} ({master_pts['sentiment_pct']}%) | Danger: {danger_level['label']} | Action: {ai_summary.get('action','?')}")
            print(f"[Market Summary] Breakdown → News: {news_pct}% | Technical: {tech_pct}% | Whale: {whale_pct}%")

            top_coins = list(coin_master_pts.items())[:3]
            if top_coins:
                coin_str = " | ".join([f"{c}: {d.get('sentiment','?')}" for c, d in top_coins])
                print(f"[Market Summary] Top coins → {coin_str}")
            print(f"[Market Summary] ✓ Saved → SQLite | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            print(f"[Market Summary] Error: {e}")

        time.sleep(300)


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    t1 = threading.Thread(target=news_agent_loop,      daemon=True, name="NewsAgent")
    t2 = threading.Thread(target=technical_agent_loop, daemon=True, name="TechAgent")
    t3 = threading.Thread(target=whale_agent_loop,     daemon=True, name="WhaleAgent")
    t4 = threading.Thread(target=market_summary_loop,  daemon=True, name="MarketSummary")

    t1.start()
    t2.start()
    t3.start()
    t4.start()

    print("All Sub-Agents running in background.")
    while True:
        time.sleep(1)

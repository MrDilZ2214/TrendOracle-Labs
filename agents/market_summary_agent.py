#!/usr/bin/env python3
"""
agents/market_summary_agent.py — Market Summary Agent v2 (SQLite-backed)
=========================================================================
Combines news + technical + whale data into weighted master points
and AI-generated market summary. Reads from SQLite kv_store.
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import requests
from datetime import datetime

import database

import config as _cfg
NVIDIA_API_KEY = _cfg.NVIDIA_API_KEY
NVIDIA_URL     = _cfg.NVIDIA_URL
MODEL          = _cfg.MODEL

HEADERS = {
    "Authorization": f"Bearer {NVIDIA_API_KEY}",
    "Content-Type":  "application/json"
}

SYSTEM_PROMPT = """You are a professional crypto market analyst sub-agent.
Your job is to produce a concise, direct market summary from the provided data.
Respond STRICTLY in this format, NO extra text:

OVERALL_SENTIMENT: STRONGLY BULLISH / BULLISH / NEUTRAL / BEARISH / STRONGLY BEARISH
KEY_POINT_1: [one sentence]
KEY_POINT_2: [one sentence]
KEY_POINT_3: [one sentence]
SUMMARY: [3-4 sentence professional market overview]
ACTION: BUY / SELL / HOLD / WAIT"""


# ══════════════════════════════════════════════════════════════════
#  DB Helpers (SQLite-backed)
# ══════════════════════════════════════════════════════════════════

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


def save_market_summary_db(db: dict):
    database.kv_set("market_summary", db)


# ══════════════════════════════════════════════════════════════════
#  Master Points Calculation
# ══════════════════════════════════════════════════════════════════

def calculate_master_points() -> dict:
    WEIGHTS = {
        "technical": 0.40,
        "news":      0.35,
        "whale":     0.25
    }

    news_db  = database.kv_get("news")
    tech_db  = database.kv_get("technical")
    whale_db = database.kv_get("whale")

    news_pts  = news_db.get("total_points",  {"up": 0, "down": 0, "total": 0})
    tech_pts  = tech_db.get("total_points",  {"up": 0, "down": 0, "total": 0})
    whale_pts = whale_db.get("total_points", {"up": 5, "down": 5, "total": 10})

    def ratio(pts):
        t = pts.get("total", 0)
        return pts.get("up", 0) / t if t > 0 else 0.5

    weighted_up_ratio = (
        ratio(news_pts)  * WEIGHTS["news"] +
        ratio(tech_pts)  * WEIGHTS["technical"] +
        ratio(whale_pts) * WEIGHTS["whale"]
    )

    combined_total = (
        news_pts.get("total", 0) +
        tech_pts.get("total", 0) +
        whale_pts.get("total", 0)
    )
    combined_up   = round(weighted_up_ratio * combined_total)
    combined_down = combined_total - combined_up

    pct = (combined_up / combined_total * 100) if combined_total > 0 else 50.0
    if pct >= 70:   sentiment = "STRONGLY BULLISH"
    elif pct >= 55: sentiment = "BULLISH"
    elif pct >= 45: sentiment = "NEUTRAL"
    elif pct >= 30: sentiment = "BEARISH"
    else:           sentiment = "STRONGLY BEARISH"

    return {
        "up":            combined_up,
        "down":          combined_down,
        "total":         combined_total,
        "sentiment":     sentiment,
        "sentiment_pct": round(pct, 1),
        "breakdown": {
            "news":      {**news_pts,  "weight": WEIGHTS["news"]},
            "technical": {**tech_pts,  "weight": WEIGHTS["technical"]},
            "whale":     {**whale_pts, "weight": WEIGHTS["whale"]}
        }
    }


# ══════════════════════════════════════════════════════════════════
#  Per-Coin Master Points
# ══════════════════════════════════════════════════════════════════

def calculate_coin_master_points() -> dict:
    news_db = database.kv_get("news")
    tech_db = database.kv_get("technical")

    news_coins = news_db.get("coin_points", {})
    tech_coins = tech_db.get("coin_points", {})

    all_coins = set(list(news_coins.keys()) + list(tech_coins.keys()))
    result    = {}

    for coin in all_coins:
        n = news_coins.get(coin, {"up": 0, "down": 0, "total": 0})
        t = tech_coins.get(coin, {"up": 0, "down": 0, "total": 0})

        total = n.get("total", 0) + t.get("total", 0)
        up    = n.get("up", 0)    + t.get("up", 0)
        down  = n.get("down", 0)  + t.get("down", 0)

        pct = (up / total * 100) if total > 0 else 50.0
        if pct >= 70:   sentiment = "STRONGLY BULLISH"
        elif pct >= 55: sentiment = "BULLISH"
        elif pct >= 45: sentiment = "NEUTRAL"
        elif pct >= 30: sentiment = "BEARISH"
        else:           sentiment = "STRONGLY BEARISH"

        result[coin] = {
            "up":            up,
            "down":          down,
            "total":         total,
            "sentiment":     sentiment,
            "sentiment_pct": round(pct, 1),
            "breakdown": {
                "news":      n,
                "technical": t
            }
        }

    return result


# ══════════════════════════════════════════════════════════════════
#  AI Summary Formatters
# ══════════════════════════════════════════════════════════════════

def format_coin_sentiments(coin_master_pts: dict) -> str:
    lines = []
    for coin, data in list(coin_master_pts.items())[:6]:
        lines.append(f"  {coin}: {data.get('sentiment', 'NEUTRAL')} ({data.get('sentiment_pct', 50):.1f}%)")
    return "\n".join(lines) if lines else "  No coin data"


def format_prices(live_prices: dict) -> str:
    lines = []
    for coin, data in list(live_prices.items())[:6]:
        p = data.get("price", 0)
        c = data.get("change_24h", 0)
        lines.append(f"  {coin}: ${p:,.4g} ({c:+.2f}%)")
    return "\n".join(lines) if lines else "  No price data"


def format_gainers(top_gainers: list) -> str:
    parts = []
    for g in top_gainers[:3]:
        sym = g.get("symbol", "")
        chg = g.get("change_percent", 0)
        parts.append(f"{sym} {chg:+.1f}%")
    return ", ".join(parts) if parts else "N/A"


def format_news_headlines(news_list: list) -> str:
    lines = []
    for n in news_list[:5]:
        if isinstance(n, dict):
            title = n.get("title", n.get("headline", ""))[:80]
            if title:
                lines.append(f"  • {title}")
    return "\n".join(lines) if lines else "  No recent news"


def format_whale_alerts(alerts: list) -> str:
    lines = []
    for a in alerts[:3]:
        if isinstance(a, dict):
            direction = a.get("direction", "UNKNOWN")
            amount    = a.get("amount_coin", "?")
            usd       = a.get("amount_usd", 0)
            desc      = str(a.get("description", ""))[:60]
            lines.append(f"  [{direction}] {amount} (${usd:,.0f}) — {desc}")
        else:
            lines.append(f"  {str(a)[:80]}")
    return "\n".join(lines) if lines else "  No whale alerts"


# ══════════════════════════════════════════════════════════════════
#  AI Summary Parse
# ══════════════════════════════════════════════════════════════════

def parse_ai_summary(raw: str) -> dict:
    lines = raw.strip().split('\n')
    result = {
        "text":              raw,
        "overall_sentiment": "NEUTRAL",
        "key_points":        [],
        "action":            "HOLD",
        "generated_at":      datetime.now().isoformat()
    }
    for line in lines:
        l = line.strip()
        if l.startswith("OVERALL_SENTIMENT:"):
            result["overall_sentiment"] = l.split(":", 1)[1].strip()
        elif l.startswith("KEY_POINT_"):
            kp = l.split(":", 1)[1].strip() if ":" in l else l
            result["key_points"].append(kp)
        elif l.startswith("SUMMARY:"):
            result["text"] = l.split(":", 1)[1].strip()
        elif l.startswith("ACTION:"):
            result["action"] = l.split(":", 1)[1].strip()
    return result


# ══════════════════════════════════════════════════════════════════
#  AI Summary Generation
# ══════════════════════════════════════════════════════════════════

def generate_ai_summary(context: dict) -> dict:
    master_pts      = context.get("master_points", {})
    danger          = context.get("danger_level", {})
    coin_master_pts = context.get("coin_master_points", {})
    live_prices     = context.get("live_prices", {})
    top_gainers     = context.get("top_gainers", [])
    latest_news     = context.get("latest_news", [])
    whale_alerts    = context.get("whale_alerts", [])

    prompt = f"""Current crypto market data:

MASTER SENTIMENT SCORE: {master_pts.get('sentiment', 'NEUTRAL')} ({master_pts.get('sentiment_pct', 50)}% bullish)
DANGER LEVEL: {danger.get('label', 'UNKNOWN')} ({danger.get('score', 0)}/10)

COIN SENTIMENTS:
{format_coin_sentiments(coin_master_pts)}

LIVE PRICES:
{format_prices(live_prices)}

TOP GAINERS: {format_gainers(top_gainers)}

LATEST NEWS HEADLINES:
{format_news_headlines(latest_news)}

WHALE ACTIVITY:
{format_whale_alerts(whale_alerts)}

Provide a concise market summary."""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens":  800
    }

    try:
        response = requests.post(NVIDIA_URL, headers=HEADERS, json=payload, timeout=60)
        response.raise_for_status()
        msg = response.json()['choices'][0]['message']
        raw = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        if raw:
            return parse_ai_summary(raw)
    except Exception as e:
        print(f"[Market Summary] AI call failed: {e}")

    return {
        "text":              "AI summary unavailable.",
        "overall_sentiment": master_pts.get("sentiment", "NEUTRAL"),
        "key_points":        [],
        "action":            "HOLD",
        "generated_at":      datetime.now().isoformat()
    }


# ══════════════════════════════════════════════════════════════════
#  Public MarketSummaryAgent class — backwards compat
# ══════════════════════════════════════════════════════════════════

class MarketSummaryAgent:
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


if __name__ == "__main__":
    agent = MarketSummaryAgent()
    print("Generating Market Summary from DB...")
    result = agent.get_market_summary()
    import json
    print(json.dumps(result, indent=2, default=str))

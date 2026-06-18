#!/usr/bin/env python3
"""
agents/whale_alert_monitor.py — Whale Alert Monitor v2 (SQLite-backed)
=======================================================================
Fetches whale-alert.io data, calculates danger level,
and saves structured whale data to SQLite kv_store.
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

URL = (
    "https://whale-alert.io/data.json"
    "?alerts=9&prices=BTC"
    "&hodl=bitcoin,BTC"
    "&potential_profit=bitcoin,BTC"
    "&average_buy_price=bitcoin,BTC"
    "&realized_profit=bitcoin,BTC"
    "&volume=bitcoin,BTC"
    "&news=true"
)


def _safe_float(val, default=0.0) -> float:
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return float(default)


# ══════════════════════════════════════════════════════════════════
#  DB Helpers (SQLite-backed)
# ══════════════════════════════════════════════════════════════════

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


def save_whale_db(db: dict):
    database.kv_set("whale", db)


# ══════════════════════════════════════════════════════════════════
#  Data Fetch
# ══════════════════════════════════════════════════════════════════

def fetch_data() -> dict:
    try:
        response = requests.get(URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Whale Agent] Fetch error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  Alert Parse
# ══════════════════════════════════════════════════════════════════

def parse_alert_string(alert_raw) -> dict:
    try:
        if isinstance(alert_raw, dict):
            return {
                "amount_coin":   str(alert_raw.get("amount", "unknown")),
                "amount_usd":    float(alert_raw.get("amount_usd", 0)),
                "description":   str(alert_raw.get("text", "")),
                "direction":     "UNKNOWN",
                "danger_weight": 1
            }

        parts = str(alert_raw).split(',')
        amount_coin    = parts[2].strip('"').strip() if len(parts) > 2 else "unknown"
        amount_usd_str = parts[3].strip('"').strip().replace('$', '').replace(',', '') if len(parts) > 3 else "0"
        description    = parts[4].strip('"').strip() if len(parts) > 4 else ""

        try:
            s = amount_usd_str
            if 'B' in s or 'b' in s:
                amount_usd = float(s.replace('B', '').replace('b', '')) * 1_000_000_000
            elif 'M' in s or 'm' in s:
                amount_usd = float(s.replace('M', '').replace('m', '')) * 1_000_000
            else:
                amount_usd = float(s)
        except:
            amount_usd = 0

        exchange_keywords = [
            "binance", "coinbase", "kraken", "okx", "bybit", "kucoin",
            "to exchange", "bitfinex", "huobi", "gemini", "bitstamp"
        ]
        wallet_keywords = [
            "from exchange", "unknown wallet", "cold wallet", "self-custody"
        ]

        desc_lower = description.lower()
        if any(k in desc_lower for k in exchange_keywords):
            direction     = "TO_EXCHANGE"
            danger_weight = 3
        elif any(k in desc_lower for k in wallet_keywords):
            direction     = "FROM_EXCHANGE"
            danger_weight = 1
        else:
            direction     = "UNKNOWN"
            danger_weight = 1

        return {
            "amount_coin":   amount_coin,
            "amount_usd":    amount_usd,
            "description":   description,
            "direction":     direction,
            "danger_weight": danger_weight
        }
    except:
        return {
            "amount_coin":   "unknown",
            "amount_usd":    0,
            "description":   str(alert_raw),
            "direction":     "UNKNOWN",
            "danger_weight": 0
        }


# ══════════════════════════════════════════════════════════════════
#  Danger Level Calculation
# ══════════════════════════════════════════════════════════════════

def calculate_danger_level(data: dict) -> dict:
    score   = 0
    reasons = []

    real_profit_change = _safe_float(data.get('realized_profit', {}).get('c', 0))
    if real_profit_change > 50:
        score += 3
        reasons.append(f"Realized profit spike +{real_profit_change:.1f}% — whales cashing out heavily")
    elif real_profit_change > 20:
        score += 2
        reasons.append(f"Realized profit up +{real_profit_change:.1f}% — some profit taking")
    elif real_profit_change > 10:
        score += 1
        reasons.append(f"Realized profit mildly elevated +{real_profit_change:.1f}%")

    vol_change = _safe_float(data.get('volume', {}).get('c', 0))
    if vol_change < -30:
        score += 2
        reasons.append(f"Volume dropped {vol_change:.1f}% — very low liquidity, high volatility risk")
    elif vol_change < -15:
        score += 1
        reasons.append(f"Volume down {vol_change:.1f}% — reduced market activity")

    alerts             = data.get('alerts', [])
    exchange_transfers = 0
    large_transfers    = 0
    for alert in alerts:
        parsed = parse_alert_string(alert)
        if parsed["direction"] == "TO_EXCHANGE":
            exchange_transfers += 1
            if parsed["amount_usd"] > 10_000_000:
                large_transfers += 1

    if large_transfers >= 3:
        score += 3
        reasons.append(f"{large_transfers} large whale transfers to exchanges (>$10M each) — heavy sell pressure incoming")
    elif large_transfers >= 1:
        score += 2
        reasons.append(f"{large_transfers} whale transfer(s) to exchange detected — potential sell pressure")
    elif exchange_transfers >= 2:
        score += 1
        reasons.append(f"{exchange_transfers} transfers to exchanges detected")

    pot_profit_change = _safe_float(data.get('potential_profit', {}).get('c', 0))
    if pot_profit_change < -20:
        score += 1
        reasons.append(f"Potential profit dropped {pot_profit_change:.1f}% — market sentiment deteriorating")

    btc_change = _safe_float(data.get('prices', {}).get('c', 0))
    if btc_change < -5:
        score += 1
        reasons.append(f"BTC dropped {btc_change:.1f}% in 24h — bearish momentum")

    hodl_change = _safe_float(data.get('hodl', {}).get('c', 0))
    if hodl_change < -2:
        score += 1
        reasons.append(f"HODLers reducing positions by {hodl_change:.1f}% — long-term holders exiting")

    score = max(0, min(10, score))

    if score >= 8:
        level = "CRITICAL"; label = "EXTREMELY DANGEROUS"
    elif score >= 6:
        level = "HIGH";     label = "DANGEROUS"
    elif score >= 4:
        level = "MEDIUM";   label = "CAUTION"
    elif score >= 2:
        level = "LOW";      label = "MILD RISK"
    else:
        level = "SAFE";     label = "SAFE"

    if not reasons:
        reasons.append("No major whale activity or market stress signals detected")

    return {"level": level, "score": score, "label": label, "reasons": reasons}


# ══════════════════════════════════════════════════════════════════
#  Up/Down Score from Danger
# ══════════════════════════════════════════════════════════════════

def calculate_up_down_from_danger(danger_score: int):
    down_score = max(1, min(9, danger_score))
    up_score   = 10 - down_score
    return up_score, down_score


# ══════════════════════════════════════════════════════════════════
#  Market Stats Parse
# ══════════════════════════════════════════════════════════════════

def parse_market_stats(data: dict) -> dict:
    return {
        "btc_price":               _safe_float(data.get('prices', {}).get('p', 0)),
        "btc_change_24h":          _safe_float(data.get('prices', {}).get('c', 0)),
        "volume_24h":              _safe_float(data.get('volume', {}).get('v', 0)),
        "volume_change":           _safe_float(data.get('volume', {}).get('c', 0)),
        "avg_buy_price":           _safe_float(data.get('average_buy_price', {}).get('p', 0)),
        "avg_buy_change":          _safe_float(data.get('average_buy_price', {}).get('c', 0)),
        "potential_profit":        _safe_float(data.get('potential_profit', {}).get('v', 0)),
        "potential_profit_change": _safe_float(data.get('potential_profit', {}).get('c', 0)),
        "realized_profit":         _safe_float(data.get('realized_profit', {}).get('v', 0)),
        "realized_profit_change":  _safe_float(data.get('realized_profit', {}).get('c', 0)),
    }


def hodl_comment(hodl_change: float) -> str:
    if hodl_change > 2:
        return "HODLers accumulating — strong long-term confidence"
    elif hodl_change > 0:
        return "HODLers slightly increasing — mild confidence"
    elif hodl_change > -2:
        return "HODLers stable — neutral sentiment"
    elif hodl_change > -5:
        return "HODLers reducing — some long-term holders exiting"
    else:
        return "HODLers significantly reducing — bearish long-term signal"


if __name__ == "__main__":
    import time
    print("Whale Alert Monitor Started — standalone mode...")
    while True:
        data = fetch_data()
        if data:
            danger = calculate_danger_level(data)
            up, down = calculate_up_down_from_danger(danger["score"])
            stats = parse_market_stats(data)
            print(f"[Whale] Danger: {danger['label']} ({danger['score']}/10) | up={up} down={down} | BTC ${stats['btc_price']:,.0f}")
        time.sleep(60)

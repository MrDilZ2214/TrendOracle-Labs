#!/usr/bin/env python3
"""
agents/trade_setup_agent.py — Trade Setup Agent v4 (SQLite-backed)
===================================================================
Data sources:
  1. OHLCV + custom indicator engine  → ATR levels / bias
  2. crypto_technical_analysis        → SignalEngine (10 sig)
  3. SQLite kv_store (technical)      → TA snapshot
  4. SQLite kv_store (news)           → news sentiment
  5. SQLite kv_store (market_summary) → combined master points
  6. HistoryAgent                     → 30-day price history
  7. EnhancedTools                    → live ticker price
  8. SQLite kv_store (whale)          → danger level + alerts
  9. NVIDIA AI                        → generate final text
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "agents"), os.path.join(ROOT_DIR, "tools"), os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import json
import re
import requests
import pandas as pd
import numpy as np
from datetime import datetime

import database

import config as _cfg
BITGET_URL     = _cfg.BITGET_BASE
NVIDIA_API_KEY = _cfg.NVIDIA_API_KEY
NVIDIA_URL     = _cfg.NVIDIA_URL
AI_MODEL       = _cfg.MODEL

_INTERVAL_MAP = {
    "1m": "1min",  "3m": "5min",  "5m": "5min",
    "15m": "15min", "30m": "30min",
    "1h": "1h",    "2h": "1h",    "4h": "4h",
    "6h": "6h",    "12h": "12h",
    "1d": "1day",  "3d": "3day",  "1w": "1week", "1M": "1M",
}


# ══════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════

def _safe_json(obj):
    if isinstance(obj, (np.integer,)):   return int(obj)
    if isinstance(obj, (np.floating,)):  return float(obj)
    if isinstance(obj, np.ndarray):      return obj.tolist()
    if isinstance(obj, pd.Series):       return obj.tolist()
    if isinstance(obj, datetime):        return obj.isoformat()
    return str(obj)


def _j(obj) -> str:
    return json.dumps(obj, default=_safe_json, ensure_ascii=False)


def _fmt(p) -> str:
    try:
        p = float(p)
        if p >= 1000:  return f"{p:,.2f}"
        if p >= 1:     return f"{p:.4f}"
        if p >= 0.01:  return f"{p:.5f}"
        return f"{p:.8f}"
    except:
        return str(p)


# ══════════════════════════════════════════════════════════════════
#  STEP 1 — LOCAL INDICATOR ENGINE  (ATR-based levels + bias)
# ══════════════════════════════════════════════════════════════════

def _fetch_ohlcv(sym: str, interval: str, limit: int = 300):
    s = sym.upper()
    if not s.endswith("USDT"): s += "USDT"
    granularity = _INTERVAL_MAP.get(interval.lower(), interval)
    try:
        r = requests.get(f"{BITGET_URL}/candles",
                         params={"symbol": s, "granularity": granularity, "limit": min(limit, 1000)},
                         timeout=15)
        r.raise_for_status()
        raw = r.json()["data"]
        df = pd.DataFrame(raw, columns=[
            "ts", "open", "high", "low", "close", "volume",
            "quoteVolume", "usdtVolume"])
        df["time"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df.reset_index(drop=True)
    except:
        return None


def _ema(s, n):  return s.ewm(span=n, adjust=False).mean()
def _rsi(c, p=14):
    d=c.diff(); g=d.where(d>0,0.0).ewm(com=p-1,adjust=False).mean()
    l=(-d.where(d<0,0.0)).ewm(com=p-1,adjust=False).mean()
    return 100-100/(1+g/l.replace(0,np.nan))
def _macd(c):
    f=c.ewm(span=12,adjust=False).mean(); s=c.ewm(span=26,adjust=False).mean()
    m=f-s; sg=m.ewm(span=9,adjust=False).mean(); return m,sg,m-sg
def _atr(h,l,c,p=14):
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(com=p-1,adjust=False).mean()
def _bb(c,w=20):
    m=c.rolling(w).mean(); s=c.rolling(w).std()
    u=m+2*s; lo=m-2*s; pct=(c-lo)/(u-lo)
    return u,m,lo,pct
def _st(h,l,c,p=10,mult=3.0):
    a=_atr(h,l,c,p); ub=(h+l)/2+mult*a; lb=(h+l)/2-mult*a
    fu=ub.copy(); fl=lb.copy(); st=pd.Series(index=c.index,dtype=float)
    for i in range(1,len(c)):
        fu.iloc[i]=ub.iloc[i] if (ub.iloc[i]<fu.iloc[i-1] or c.iloc[i-1]>fu.iloc[i-1]) else fu.iloc[i-1]
        fl.iloc[i]=lb.iloc[i] if (lb.iloc[i]>fl.iloc[i-1] or c.iloc[i-1]<fl.iloc[i-1]) else fl.iloc[i-1]
        pst=st.iloc[i-1]
        if pst==fu.iloc[i-1]: st.iloc[i]=fl.iloc[i] if c.iloc[i]>fu.iloc[i] else fu.iloc[i]
        else:                  st.iloc[i]=fu.iloc[i] if c.iloc[i]<fl.iloc[i] else fl.iloc[i]
    return st,pd.Series(np.where(c>st,1,-1),index=c.index)
def _vwap(h,l,c,v): tp=(h+l+c)/3; return (tp*v).cumsum()/v.cumsum()
def _obv(c,v): return (np.sign(c.diff().fillna(0))*v).cumsum()


def _calc_levels(df: pd.DataFrame, price: float) -> dict:
    c=df["close"]; h=df["high"]; l=df["low"]; v=df["volume"]
    e9=_ema(c,9); e20=_ema(c,20); e21=_ema(c,21); e50=_ema(c,50); e200=_ema(c,200)
    rsi=_rsi(c); ml,ms,mh=_macd(c); atr=_atr(h,l,c)
    bbu,bbm,bbl,bbp=_bb(c); st,std=_st(h,l,c)
    vwap=_vwap(h,l,c,v); obv=_obv(c,v); obvsma=obv.rolling(20).mean()
    piv=(h.iloc[-2]+l.iloc[-2]+c.iloc[-2])/3
    r1=2*piv-l.iloc[-2]; r2=piv+(h.iloc[-2]-l.iloc[-2])
    s1=2*piv-h.iloc[-2]; s2=piv-(h.iloc[-2]-l.iloc[-2])

    sc=[]
    ema_bull=price>e9.iloc[-1]>e21.iloc[-1]>e50.iloc[-1]
    ema_bear=price<e9.iloc[-1]<e21.iloc[-1]<e50.iloc[-1]
    sc.append(1 if ema_bull else (-1 if ema_bear else 0))
    sc.append(1 if price>e200.iloc[-1] else -1)
    sc.append(1 if e20.iloc[-1]>e50.iloc[-1] else -1)
    macd_bull=ml.iloc[-1]>ms.iloc[-1] and mh.iloc[-1]>0
    macd_bear=ml.iloc[-1]<ms.iloc[-1] and mh.iloc[-1]<0
    sc.append(1 if macd_bull else (-1 if macd_bear else 0))
    rn=rsi.iloc[-1]; sc.append(1 if rn<35 else (-1 if rn>65 else 0))
    sc.append(int(std.iloc[-1]))
    sc.append(1 if price>vwap.iloc[-1] else -1)
    sc.append(1 if obv.iloc[-1]>obvsma.iloc[-1] else -1)
    bp=bbp.iloc[-1]; sc.append(1 if bp<0.2 else (-1 if bp>0.8 else 0))
    sc.append(1 if price>piv else -1)

    tot=sum(sc); n=len(sc)
    if   tot>=4:  bias="LONG";    conf=min(50+tot*6,92)
    elif tot>=2:  bias="LONG";    conf=min(50+tot*4,78)
    elif tot<=-4: bias="SHORT";   conf=min(50+abs(tot)*6,92)
    elif tot<=-2: bias="SHORT";   conf=min(50+abs(tot)*4,78)
    else:         bias="NEUTRAL"; conf=45

    av=float(atr.iloc[-1])
    if bias=="LONG":
        el=price*0.997; eh=price*1.002
        sl=min(price-1.6*av, float(s1)*0.994)
        tp1=price+1.0*av; tp2=price+2.0*av; tp3=price+3.5*av
        inv=sl*0.998
    elif bias=="SHORT":
        el=price*0.998; eh=price*1.003
        sl=max(price+1.6*av, float(r1)*1.006)
        tp1=price-1.0*av; tp2=price-2.0*av; tp3=price-3.5*av
        inv=sl*1.002
    else:
        el=eh=sl=tp1=tp2=tp3=inv=price

    rr1=abs(tp1-price)/max(abs(price-sl),1e-9)
    rr2=abs(tp2-price)/max(abs(price-sl),1e-9)

    return {
        "bias": bias, "confidence": conf, "score": tot,
        "buy_pct": round(sc.count(1)/n*100), "sell_pct": round(sc.count(-1)/n*100),
        "price": _fmt(price),
        "entry_zone": f"{_fmt(el)} – {_fmt(eh)}",
        "stop_loss": _fmt(sl), "tp1": _fmt(tp1), "tp2": _fmt(tp2), "tp3": _fmt(tp3),
        "rr1": f"1:{rr1:.2f}", "rr2": f"1:{rr2:.2f}",
        "invalidation": _fmt(inv),
        "rsi": round(float(rn),1),
        "macd_hist": round(float(mh.iloc[-1]),6),
        "ema20": _fmt(e20.iloc[-1]), "ema50": _fmt(e50.iloc[-1]), "ema200": _fmt(e200.iloc[-1]),
        "bb_pct": round(float(bp),2), "vwap": _fmt(vwap.iloc[-1]),
        "st_dir": int(std.iloc[-1]),
        "obv_bull": bool(obv.iloc[-1]>obvsma.iloc[-1]),
        "atr": _fmt(av),
        "pivot": _fmt(piv), "r1": _fmt(r1), "r2": _fmt(r2),
        "s1": _fmt(s1), "s2": _fmt(s2),
    }


# ══════════════════════════════════════════════════════════════════
#  STEP 2 — SignalEngine
# ══════════════════════════════════════════════════════════════════

def _get_signal_engine(sym: str) -> dict:
    try:
        import crypto_technical_analysis as cta
        symbol_usdt = sym.upper() + "USDT" if "USDT" not in sym.upper() else sym.upper()
        df = cta.fetch_ohlcv(symbol_usdt)
        if df is None:
            return {}
        engine = cta.SignalEngine(df)
        result = engine.get_signals()
        return {
            "consensus": result.get("consensus", "NEUTRAL"),
            "score":     result.get("total_score", 0),
            "buy":       result.get("buy_count", 0),
            "sell":      result.get("sell_count", 0),
            "pct_buy":   round(result.get("pct_buy", 0), 1),
            "pct_sell":  round(result.get("pct_sell", 0), 1),
        }
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
#  STEP 3 — Technical DB snapshot (SQLite)
# ══════════════════════════════════════════════════════════════════

def _get_technical_data(sym: str) -> dict:
    sym = sym.upper().replace("USDT", "")
    db  = database.kv_get("technical")

    snapshot = db.get("snapshots", {}).get(sym, {})
    if not snapshot:
        return {"status": "no_data", "consensus": "NEUTRAL", "up_score": 5, "down_score": 5,
                "buy_count": 0, "sell_count": 0, "pct_buy": 50, "pct_sell": 50,
                "tp": None, "sl": None, "rr": None, "signals": {}, "coin_points": {}, "freshness": "no data"}

    fresh = "ok"
    if snapshot.get("timestamp"):
        try:
            age = (datetime.now() - datetime.fromisoformat(snapshot["timestamp"])).total_seconds()
            if age > 300:
                fresh = f"stale ({int(age/60)}min old)"
        except:
            pass

    signals_summary = {}
    for name, data in snapshot.get("signals", {}).items():
        signals_summary[name] = data.get("signal", "?")

    return {
        "consensus":   snapshot.get("consensus", "NEUTRAL"),
        "up_score":    snapshot.get("up_score", 5),
        "down_score":  snapshot.get("down_score", 5),
        "buy_count":   snapshot.get("buy_count", 0),
        "sell_count":  snapshot.get("sell_count", 0),
        "pct_buy":     snapshot.get("pct_buy", 50),
        "pct_sell":    snapshot.get("pct_sell", 50),
        "tp":          snapshot.get("tp"),
        "sl":          snapshot.get("sl"),
        "rr":          snapshot.get("rr"),
        "signals":     signals_summary,
        "coin_points": db.get("coin_points", {}).get(sym, {}),
        "freshness":   fresh
    }


# ══════════════════════════════════════════════════════════════════
#  STEP 4 — News data (SQLite)
# ══════════════════════════════════════════════════════════════════

def _get_news_data(sym: str) -> dict:
    sym = sym.upper().replace("USDT", "")
    db  = database.kv_get("news")

    all_news  = db.get("news", [])
    coin_news = [n for n in all_news if sym in n.get("affected_coins", [])]

    if not coin_news:
        coin_news = [n for n in all_news if "BTC" in n.get("affected_coins", [])]

    coin_news = sorted(coin_news, key=lambda x: x.get("timestamp", ""), reverse=True)[:3]

    news_lines = []
    for n in coin_news:
        up     = n.get("up_score", 5)
        down   = n.get("down_score", 5)
        title  = n.get("title", "")[:80]
        impact = n.get("impact", "N/A")
        news_lines.append(f"UP={up}/DOWN={down} [{impact}]: {title}")

    coin_pts  = db.get("coin_points", {}).get(sym, {"up": 0, "down": 0, "total": 0})
    total_pts = db.get("total_points", {"up": 0, "down": 0, "total": 0})

    return {
        "coin_points":  coin_pts,
        "total_points": total_pts,
        "news_items":   news_lines,
        "news_count":   len(coin_news)
    }


# ══════════════════════════════════════════════════════════════════
#  STEP 5 — Combined points (SQLite)
# ══════════════════════════════════════════════════════════════════

def _get_points_combined(sym: str) -> dict:
    sym       = sym.upper().replace("USDT", "")
    news_pts  = database.kv_get("news").get("coin_points", {}).get(sym, {})
    tech_pts  = database.kv_get("technical").get("coin_points", {}).get(sym, {})
    coin_mstr = database.kv_get("market_summary").get("coin_master_points", {}).get(sym, {})

    return {
        "news_points":      news_pts,
        "technical_points": tech_pts,
        "master_points":    coin_mstr,
        "sentiment":        coin_mstr.get("sentiment", "NEUTRAL"),
        "sentiment_pct":    coin_mstr.get("sentiment_pct", 50.0),
        "breakdown":        coin_mstr.get("breakdown", {})
    }


# ══════════════════════════════════════════════════════════════════
#  STEP 6 — 30-day history
# ══════════════════════════════════════════════════════════════════

def _get_history(sym: str) -> dict:
    try:
        from history_agent import get_price_history
        hist = get_price_history(sym, 30)
        if isinstance(hist, list) and len(hist) >= 2:
            prices = [h["close"] for h in hist if "close" in h]
            first  = prices[0] if prices else None
            last   = prices[-1] if prices else None
            high30 = max(prices) if prices else None
            low30  = min(prices) if prices else None
            chg30  = round((last-first)/first*100,2) if first and last else None
            return {
                "30d_change": f"{chg30}%" if chg30 is not None else "N/A",
                "30d_high":   _fmt(high30) if high30 else "N/A",
                "30d_low":    _fmt(low30) if low30 else "N/A",
            }
        return {"note": "No history data"}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
#  STEP 7 — EnhancedTools: live price
# ══════════════════════════════════════════════════════════════════

def _get_live_price(sym: str) -> dict:
    try:
        from enhanced_tools import EnhancedTools
        et = EnhancedTools()
        r  = et.get_live_price(sym)
        return {"price": _fmt(r.get("price", 0)), "symbol": r.get("symbol", "")}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
#  STEP 8 — Whale data (SQLite)
# ══════════════════════════════════════════════════════════════════

def _get_whale_data() -> dict:
    db        = database.kv_get("whale")
    danger    = db.get("danger_level", {"level": "UNKNOWN", "score": 0, "label": "NO DATA"})
    stats     = db.get("market_stats", {})
    alerts    = db.get("alerts", [])[:3]
    whale_pts = db.get("total_points", {"up": 5, "down": 5})

    alert_lines = []
    for a in alerts:
        if isinstance(a, dict):
            direction = a.get("direction", "UNKNOWN")
            amount    = a.get("amount_coin", "?")
            usd       = a.get("amount_usd", 0)
            desc      = str(a.get("description", ""))[:60]
            alert_lines.append(f"[{direction}] {amount} (${usd:,.0f}) — {desc}")
        else:
            alert_lines.append(str(a)[:80])

    return {
        "danger_label":           danger.get("label", "NO DATA"),
        "danger_score":           danger.get("score", 0),
        "danger_reasons":         danger.get("reasons", [])[:2],
        "whale_points":           whale_pts,
        "btc_price":              stats.get("btc_price", 0),
        "btc_change_24h":         stats.get("btc_change_24h", 0),
        "volume_change":          stats.get("volume_change", 0),
        "realized_profit_change": stats.get("realized_profit_change", 0),
        "hodl_comment":           db.get("hodl_stats", {}).get("comment", ""),
        "alerts":                 alert_lines
    }


# ══════════════════════════════════════════════════════════════════
#  STEP 9 — Market summary (SQLite)
# ══════════════════════════════════════════════════════════════════

def _get_market_summary_data(sym: str) -> dict:
    sym         = sym.upper().replace("USDT", "")
    db          = database.kv_get("market_summary")
    master      = db.get("master_points", {"sentiment": "NEUTRAL", "sentiment_pct": 50})
    coin_master = db.get("coin_master_points", {}).get(sym, {})
    ai_summary  = db.get("ai_summary", {})
    top_gainers = db.get("top_gainers", [])[:3]

    gainers_fmt = [
        f"{g.get('symbol', '')} {g.get('change_percent', 0):+.1f}%"
        for g in top_gainers
    ]

    return {
        "market_sentiment":     master.get("sentiment", "NEUTRAL"),
        "market_sentiment_pct": master.get("sentiment_pct", 50),
        "coin_sentiment":       coin_master.get("sentiment", "NEUTRAL"),
        "coin_sentiment_pct":   coin_master.get("sentiment_pct", 50),
        "coin_master_points":   coin_master,
        "ai_action":            ai_summary.get("action", "HOLD"),
        "ai_key_points":        ai_summary.get("key_points", []),
        "ai_summary_text":      str(ai_summary.get("text", ""))[:200],
        "top_gainers":          gainers_fmt,
        "danger_level":         db.get("danger_level", {}).get("label", "UNKNOWN")
    }


# ══════════════════════════════════════════════════════════════════
#  NVIDIA AI
# ══════════════════════════════════════════════════════════════════

TRADE_SETUP_SYSTEM = """You are an elite crypto trade setup formatter.

You receive data from ALL available analysis tools and sub-agents:
  - Live OHLCV indicator engine (EMA/RSI/MACD/ATR/SuperTrend/VWAP/OBV/Bollinger)
  - Full 10-indicator SignalEngine consensus
  - Live and cached news with sentiment analysis
  - AI point score history
  - 30-day price history
  - Live price feed
  - Whale alert transactions
  - Top market gainers
  - Market summary

Your job: synthesise ALL of this into ONE clean trade setup block for Telegram.

STRICT OUTPUT RULES — follow EXACTLY, no exceptions:
1. Output ONLY the trade setup block. No preamble, no explanation, no extra commentary.
2. Use ONLY these Telegram HTML tags: <b>, <i>, <code>. No other HTML or markdown.
3. Follow the EXACT template below — same line order, same emoji, same labels.
4. All price values must be in <code> tags.
5. Confidence bar: use █ for filled, ░ for empty — always exactly 10 characters.
6. Setup Reasons: EXACTLY 5 bullet points using •. Each is ONE concise line.
   Weave in news sentiment, whale activity, historical trend, and TA naturally.
7. Risk Note: always ONE line — practical, specific to the setup.
8. Disclaimer: always the last line, in <i> tags.
9. Do NOT add any lines outside the template.
10. Separator is always exactly: ──────────────────────────────────

TEMPLATE:
🎯 <b>TRADE SETUP — [SYMBOL]/USDT | [TF] | [BIAS_EMOJI] [BIAS]</b>
──────────────────────────────────

📊 <b>Market Tone:</b> [one sharp line: integrate news + TA consensus + whale context]
🔥 <b>Confidence:</b> [XX]%  <code>[10-char █░ bar]</code>

💰 <b>Current Price:</b>  <code>[price]</code>
📍 <b>Entry Zone:</b>     <code>[entry_low – entry_high]</code>
🛑 <b>Stop Loss:</b>      <code>[sl]</code>

🎯 <b>TP1:</b>  <code>[tp1]</code>   R:R [rr1]
🎯 <b>TP2:</b>  <code>[tp2]</code>   R:R [rr2]
🎯 <b>TP3:</b>  <code>[tp3]</code>   (runner)

📝 <b>Setup Reasons:</b>
  • [TA structure reason — EMAs, trend alignment]
  • [Momentum reason — RSI/MACD/SuperTrend]
  • [Volume/VWAP/OBV confluence reason]
  • [News sentiment + whale activity reason]
  • [Historical trend + AI score reason]

⚡ <b>Risk Level:</b> [LOW/MEDIUM/HIGH — one sentence]
❌ <b>Invalidation:</b> Close [below/above] <code>[invalidation price]</code>

📌 <b>Risk Note:</b> [specific risk management advice for this setup]

<i>⚠️ This is NOT financial advice. Trade at your own risk. NFA.</i>"""


def _call_ai(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type":  "application/json"
    }

    def _strip_thinking(text: str) -> str:
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL | re.IGNORECASE)
        return text.strip()

    def _try(model, thinking_disabled=False):
        payload = {
            "model":       model,
            "messages":    [
                {"role": "system", "content": TRADE_SETUP_SYSTEM},
                {"role": "user",   "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens":  4000,
        }
        if thinking_disabled:
            payload["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
        try:
            r = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            data    = r.json()
            msg     = data["choices"][0]["message"]
            content = _strip_thinking((msg.get("content") or "").strip())
            if content:
                return content
            rc = _strip_thinking((msg.get("reasoning_content") or "").strip())
            if rc:
                idx = rc.rfind("\U0001f3af")
                if idx != -1:
                    return rc[idx:].strip()
                return rc[-2000:].strip()
        except Exception:
            pass
        return ""

    result = _try(AI_MODEL, thinking_disabled=True)
    if result: return result
    result = _try(AI_MODEL)
    if result: return result
    result = _try("meta/llama-3.3-70b-instruct")
    if result: return result
    return "[AI generation failed: all attempts returned empty content]"


# ══════════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def get_trade_setup(symbol: str, timeframe: str = "4h") -> dict:
    sym = symbol.upper().replace("USDT", "")
    tf  = timeframe.lower()

    df = _fetch_ohlcv(sym, tf, 300)
    if df is None or len(df) < 50:
        return {"text": None, "error": f"Could not fetch OHLCV for {sym}USDT on {tf}"}

    live_price_data = _get_live_price(sym)
    try:
        price = float(str(live_price_data.get("price", "0")).replace(",", ""))
        if price == 0:
            raise ValueError
    except:
        price = float(df.iloc[-1]["close"])

    ta          = _calc_levels(df, price)
    sig_engine  = _get_signal_engine(sym)
    tech_data   = _get_technical_data(sym)
    news_data   = _get_news_data(sym)
    points_data = _get_points_combined(sym)
    history     = _get_history(sym)
    whale_data  = _get_whale_data()
    summary_data= _get_market_summary_data(sym)

    bias_emoji = "🟢" if ta["bias"] == "LONG" else ("🔴" if ta["bias"] == "SHORT" else "⚖️")
    conf_bar   = "█" * (ta["confidence"] // 10) + "░" * (10 - ta["confidence"] // 10)

    prompt = f"""Generate the trade setup for {sym}/USDT on the {tf.upper()} timeframe.

═══ CALCULATED TRADE LEVELS ═══
Symbol:        {sym}/USDT
Timeframe:     {tf.upper()}
Bias:          {ta['bias']} {bias_emoji}
Confidence:    {ta['confidence']}%  [{conf_bar}]
Score:         {ta['score']}  (buy {ta['buy_pct']}% | sell {ta['sell_pct']}%)

Current Price: {ta['price']}
Entry Zone:    {ta['entry_zone']}
Stop Loss:     {ta['stop_loss']}
TP1:           {ta['tp1']}   R:R {ta['rr1']}
TP2:           {ta['tp2']}   R:R {ta['rr2']}
TP3:           {ta['tp3']}   (runner)
Invalidation:  {ta['invalidation']}

═══ KEY INDICATORS ═══
RSI(14):       {ta['rsi']}
MACD Hist:     {ta['macd_hist']}
EMA20/50/200:  {ta['ema20']} / {ta['ema50']} / {ta['ema200']}
BB %B:         {ta['bb_pct']}
VWAP:          {ta['vwap']}
SuperTrend:    {'BULLISH ↑' if ta['st_dir'] == 1 else 'BEARISH ↓'}
OBV:           {'Accumulation ↑' if ta['obv_bull'] else 'Distribution ↓'}
ATR(14):       {ta['atr']}
Pivot:         {ta['pivot']}  R1:{ta['r1']}  R2:{ta['r2']}  S1:{ta['s1']}  S2:{ta['s2']}

═══ SIGNAL ENGINE (10-indicator consensus) ═══
{_j(sig_engine)}

═══ TECHNICAL DB SNAPSHOT ═══
Consensus:     {tech_data['consensus']}
Up/Down:       {tech_data['up_score']}/{tech_data['down_score']}
Buy/Sell:      {tech_data['buy_count']}/{tech_data['sell_count']} ({tech_data['pct_buy']}% buy)
Signals:       {_j(tech_data['signals'])}
Coin Points:   {_j(tech_data['coin_points'])}
Freshness:     {tech_data['freshness']}

═══ NEWS SENTIMENT ═══
Coin Points:   UP={news_data['coin_points'].get('up', 0)} DOWN={news_data['coin_points'].get('down', 0)} TOTAL={news_data['coin_points'].get('total', 0)}
Market Total:  UP={news_data['total_points'].get('up', 0)} DOWN={news_data['total_points'].get('down', 0)}
Latest News:
{chr(10).join(news_data['news_items']) or 'No recent news'}

═══ COMBINED POINTS SCORE (master) ═══
Coin Sentiment:     {points_data['sentiment']} ({points_data['sentiment_pct']}% bullish)
News Points:        {_j(points_data['news_points'])}
Technical Points:   {_j(points_data['technical_points'])}
Master Points:      {_j(points_data['master_points'])}

═══ 30-DAY PRICE HISTORY ═══
{_j(history)}

═══ WHALE INTELLIGENCE ═══
Danger Level:       {whale_data['danger_label']} ({whale_data['danger_score']}/10)
Danger Reasons:     {'; '.join(whale_data['danger_reasons'])}
Whale Points:       UP={whale_data['whale_points'].get('up', 5)} DOWN={whale_data['whale_points'].get('down', 5)}
BTC 24h Change:     {whale_data['btc_change_24h']}%
Volume Change:      {whale_data['volume_change']}%
Realized Profit Δ: {whale_data['realized_profit_change']}%
HODLer Comment:     {whale_data['hodl_comment']}
Recent Alerts:
{chr(10).join(whale_data['alerts']) or 'No recent whale alerts'}

═══ MARKET SUMMARY ═══
Market Sentiment:   {summary_data['market_sentiment']} ({summary_data['market_sentiment_pct']}%)
Coin Sentiment:     {summary_data['coin_sentiment']} ({summary_data['coin_sentiment_pct']}%)
AI Recommended:     {summary_data['ai_action']}
AI Key Points:      {'; '.join(summary_data['ai_key_points'])}
Top Gainers:        {', '.join(summary_data['top_gainers'])}
Danger Level:       {summary_data['danger_level']}

Now generate the trade setup block following the template EXACTLY.
Bias emoji to use: {bias_emoji}
Confidence bar to use: <code>{conf_bar}</code>"""

    text = _call_ai(prompt)
    return {"text": text, "error": None, "_sym": sym, "_tf": tf.upper()}


# ══════════════════════════════════════════════════════════════════
#  FORMAT HELPER  (called by bot.py)
# ══════════════════════════════════════════════════════════════════

def format_trade_setup_message(setup: dict) -> str:
    if setup.get("error") and not setup.get("text"):
        return f"❌ <b>Trade Setup Error</b>\n{setup['error']}"
    text = setup.get("text", "")
    if not text:
        return "❌ <b>Trade Setup Error</b>\nAI returned no content."
    return text

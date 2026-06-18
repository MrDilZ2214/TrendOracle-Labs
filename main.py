"""
main.py — Crypto AI Web Server (Real-Time Dashboard Edition)
=============================================================
Features:
  • Real-time BTC price via Bitget API (every 5s)
  • Live top gainers / market overview (every 30s)
  • Dashboard WebSocket pushes price_tick + memory_snapshot
  • All background agents patched with status reporting
  • SQLite-backed storage (replaces all JSON files)
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in [ROOT_DIR,
           os.path.join(ROOT_DIR, "agents"),
           os.path.join(ROOT_DIR, "tools"),
           os.path.join(ROOT_DIR, "core")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import json
import re
import base64
import requests
import threading
import time as _time
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import database
import auth_utils
import trade_manager

# ─────────────────────────────────────────────
#  CONFIG (from config.py)
# ─────────────────────────────────────────────
import config as _cfg
NVIDIA_API_KEY = _cfg.NVIDIA_API_KEY
NVIDIA_URL     = _cfg.NVIDIA_URL
MODEL          = _cfg.MODEL
BITGET_BASE    = _cfg.BITGET_BASE

_INTERVAL_MAP = {
    "1m": "1min",  "3m": "5min",  "5m": "5min",
    "15m": "15min", "30m": "30min",
    "1h": "1h",    "2h": "1h",    "4h": "4h",
    "6h": "6h",    "12h": "12h",
    "1d": "1day",  "3d": "3day",  "1w": "1week", "1M": "1M",
}
CHART_DIR   = os.path.join(ROOT_DIR, "charts")
MAX_HISTORY = 30

os.makedirs(CHART_DIR, exist_ok=True)

# ─────────────────────────────────────────────
#  REAL-TIME PRICE CACHE (Bitget)
# ─────────────────────────────────────────────
_price_cache: dict = {}
_market_cache: dict = {}
_price_cache_lock = threading.Lock()

TRACKED_SYMBOLS = [
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


def _bitget_fetch_tickers(symbols: list[str] | None = None) -> list[dict]:
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
        _time.sleep(5)


def _refresh_market_cache():
    while True:
        try:
            all_tickers = _bitget_fetch_tickers()
            usdt = [t for t in all_tickers if t.get("symbol", "").endswith("USDT")]

            gainers = sorted(usdt, key=lambda x: float(x.get("change24h", 0)), reverse=True)[:10]
            top_gainers = [{
                "symbol": g["symbol"].replace("USDT", ""),
                "price":  float(g.get("lastPr", 0)),
                "change": round(float(g.get("change24h", 0)) * 100, 2),
                "volume": float(g.get("usdtVolume", 0)),
            } for g in gainers]

            losers = sorted(usdt, key=lambda x: float(x.get("change24h", 0)))[:5]
            top_losers = [{
                "symbol": l["symbol"].replace("USDT", ""),
                "price":  float(l.get("lastPr", 0)),
                "change": round(float(l.get("change24h", 0)) * 100, 2),
            } for l in losers]

            total_vol = sum(float(t.get("usdtVolume", 0)) for t in usdt)

            with _price_cache_lock:
                _market_cache["top_gainers"]  = top_gainers
                _market_cache["top_losers"]   = top_losers
                _market_cache["total_volume"] = total_vol
                _market_cache["ts"]           = datetime.now().isoformat()
                _market_cache["pairs_count"]  = len(usdt)

        except Exception as e:
            print(f"[MarketCache] Error: {e}")
        _time.sleep(30)


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


# ─────────────────────────────────────────────
#  SYSTEM PROMPT
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are TrendOracle — an elite AI Crypto Trading Strategist with real-time access to live prices, technical analysis, news sentiment, whale intelligence, and trade setup generation across ALL major cryptocurrencies.

══ CRITICAL RULES — NEVER VIOLATE ══

1. ALWAYS call tools FIRST before giving ANY advice, price, or signal. Never answer from memory alone.

2. COIN-SPECIFIC REQUESTS: When the user mentions a specific coin (SOL, XRP, ADA, DOT, AVAX, LINK, MATIC, DOGE, LTC, ATOM, or ANY other coin), you MUST:
   - Call get_live_price(symbol="<THAT_COIN>") for the live price
   - Call get_technical_analysis(symbol="<THAT_COIN>") for signals
   - Do NOT default to BTC when a different coin is clearly specified

3. TRADE SETUP REQUESTS: When user asks for a "trade setup", "entry point", "how to trade", or "analyze [COIN]":
   - ALWAYS call get_trade_setup(symbol="<COIN>", timeframe="4h")
   - Then synthesize the result into your response

4. GENERAL MARKET QUESTIONS: Call get_market_summary() first, then add specific coin data if needed.

5. SUPPORTED COINS: BTC, ETH, SOL, BNB, XRP, ADA, DOT, AVAX, LINK, MATIC, DOGE, LTC, ATOM, UNI, NEAR, FTM, SAND, MANA, AXS, HBAR, ICP, FIL, VET, XLM, TRX, EOS, ALGO, and any other symbol the user mentions.

6. Give DIRECT advice: Buy at $X / Sell at $X / Hold / Wait for confirmation — with a clear one-sentence reason.

══ RESPONSE FORMAT — STRICT ══
- Flowing prose, 1–3 short paragraphs MAX.
- HTML only: <b>bold</b>, <i>italic</i>, <code>code</code>. ZERO markdown (no **, ##, *, -, lists).
- Do NOT mention tool names or what you fetched. Use the data naturally.
- Match the user's language (Sinhala, English, etc.).
- End with one clear recommendation.
"""

# ─────────────────────────────────────────────
#  TOOL SCHEMAS
# ─────────────────────────────────────────────
TOOLS_SCHEMA = [
    {"type":"function","function":{"name":"get_crypto_history","description":"Get the last 30 days of daily price history for a cryptocurrency.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker e.g. BTC, ETH"}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"get_latest_news","description":"Get analyzed cryptocurrency news and sentiment from news database. Returns news with up/down scores and coin points.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Optional coin filter e.g. BTC"},"limit":{"type":"integer","description":"Number of results (default 5)"}}}}},
    {"type":"function","function":{"name":"fetch_and_analyze_asset_news","description":"Live-fetch and AI-analyze the latest news for a specific coin.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker e.g. BTC"}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"get_technical_analysis","description":"Get technical analysis snapshot for a specific coin. Returns consensus signal (BUY/SELL/NEUTRAL), all 10 indicator signals, TP/SL targets, and up/down scores.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker e.g. BTC, ETH, SOL. USDT suffix optional."}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"get_all_technicals","description":"Get technical analysis snapshots for ALL tracked coins at once.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_current_points","description":"Get the master sentiment score and breakdown for a specific coin.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker e.g. BTC"}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"get_whale_data","description":"Get whale market intelligence — large wallet movements, danger level, BTC stats.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_live_price","description":"Get the current live price of a cryptocurrency from Bitget.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker e.g. BTC, ETH"}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"get_top_gainers","description":"Get the top gaining cryptocurrencies from Bitget 24h ticker.","parameters":{"type":"object","properties":{"limit":{"type":"integer","description":"Number of results (default 5)"}}}}},
    {"type":"function","function":{"name":"get_market_summary","description":"Get comprehensive market overview — master sentiment score, per-coin sentiment, live prices, top gainers, danger level, and AI-generated summary.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"generate_chart","description":"Generate a Bitget-style candlestick chart image for a cryptocurrency.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker symbol e.g. BTC, ETH, SOL"},"interval":{"type":"string","description":"Candle interval. Options: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w. Default 4h."},"limit":{"type":"integer","description":"Number of candles to show (default 80, max 200)"}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"get_trade_setup","description":"Generate a complete, structured trade setup for a cryptocurrency — entry zone, stop loss, TP1/TP2/TP3, R:R ratio, confidence score, and setup reasons.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"Ticker symbol e.g. BTC, ETH, SOL, XRP"},"timeframe":{"type":"string","description":"Analysis timeframe. Options: 15m, 30m, 1h, 4h, 1d. Default 4h."}},"required":["symbol"]}}},
    {"type":"function","function":{"name":"propose_trade","description":"Propose a trade for the user to confirm before execution. Use when user asks to PLACE, OPEN, or EXECUTE a trade/order.","parameters":{"type":"object","properties":{"symbol":{"type":"string","description":"e.g. BTCUSDT"},"side":{"type":"string","enum":["buy","sell"]},"size":{"type":"number","description":"Trade size in USDT."},"price":{"type":"number","description":"Single limit price (omit for market order)"},"entry_low":{"type":"number","description":"Lower bound of entry zone"},"entry_high":{"type":"number","description":"Upper bound of entry zone"},"sl":{"type":"number","description":"Stop-loss price"},"tp":{"type":"number","description":"Take-profit price"},"reason":{"type":"string"}},"required":["symbol","side","size"]}}},
    {"type":"function","function":{"name":"get_user_trade_history","description":"Get the user's trading account data including open positions, closed trade history, total P&L, win rate, balance.","parameters":{"type":"object","properties":{"limit":{"type":"integer","description":"Number of recent closed trades to include. Default 10."}}}}},
    {"type":"function","function":{"name":"analyze_trade_aging","description":"Analyze all currently open positions for aging — how long each trade has been open.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_risk_status","description":"Get the user's current portfolio risk status — open exposure, drawdown, risk score, and whether it is safe to open new positions.","parameters":{"type":"object","properties":{}}}}
]

# ─────────────────────────────────────────────
#  TOOL ICONS
# ─────────────────────────────────────────────
TOOL_ICONS = {
    "get_crypto_history":           "📈",
    "get_latest_news":              "📰",
    "fetch_and_analyze_asset_news": "🔍",
    "get_technical_analysis":       "⚡",
    "get_all_technicals":           "📡",
    "get_current_points":           "🎯",
    "get_whale_data":               "🐋",
    "get_live_price":               "💲",
    "get_top_gainers":              "🚀",
    "get_market_summary":           "🌍",
    "generate_chart":               "📊",
    "get_trade_setup":              "🎯",
    "propose_trade":                "📝",
    "get_user_trade_history":       "📋",
    "analyze_trade_aging":          "⏳",
    "get_risk_status":              "🛡️",
}

# ─────────────────────────────────────────────
#  CHART GENERATOR (Bitget OHLCV)
# ─────────────────────────────────────────────
BG_MAIN   = "#0C0E14"
BG_PANEL  = "#131722"
BG_TOP    = "#1C2030"
GREEN     = "#26A69A"
RED       = "#EF5350"
VOL_GREEN = "#1B5E54"
VOL_RED   = "#7B1B1B"
MA7_COL   = "#F6C90E"
MA25_COL  = "#B96EFE"
MA99_COL  = "#F79A39"
TEXT_COL  = "#B2B5BE"
GRID_COL  = "#1E2333"
WHITE     = "#E0E3EB"
INTERVAL_LABELS = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","4h":"4H","1d":"1D","1w":"1W"}


def _fetch_ohlcv(symbol, interval, limit):
    sym = symbol.upper()
    if not sym.endswith("USDT"): sym += "USDT"
    granularity = _INTERVAL_MAP.get(interval.lower(), interval)
    try:
        r = requests.get(f"{BITGET_BASE}/candles",
                         params={"symbol": sym, "granularity": granularity, "limit": min(limit, 1000)},
                         timeout=15)
        r.raise_for_status()
        raw = r.json()["data"]
        df = pd.DataFrame(raw, columns=["open_time","open","high","low","close","volume","quoteVolume","usdtVolume"])
        for col in ["open","high","low","close","volume"]: df[col] = df[col].astype(float)
        df["dt"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms")
        return df
    except Exception as e:
        print(f"[Chart] OHLCV fetch error: {e}"); return None


def _ma(series, n): return series.rolling(n).mean()


def generate_chart(symbol, interval="4h", limit=80):
    limit = min(int(limit), 200)
    sym   = symbol.upper().replace("USDT","")
    iv    = interval.lower()
    df = _fetch_ohlcv(sym, iv, limit)
    if df is None or df.empty: return {"status":"error","message":f"Could not fetch data for {sym}"}
    x=range(len(df)); o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values; v=df["volume"].values
    ma7=_ma(df["close"],7).values; ma25=_ma(df["close"],25).values; ma99=_ma(df["close"],99).values
    price_now=c[-1]; price_chg=(c[-1]-o[0])/o[0]*100; chg_col=GREEN if price_chg>=0 else RED
    chg_sign="+" if price_chg>=0 else ""; iv_label=INTERVAL_LABELS.get(iv,iv.upper())
    fig=plt.figure(figsize=(14,8),facecolor=BG_MAIN)
    gs=gridspec.GridSpec(5,1,hspace=0.0,height_ratios=[0.55,3.5,0.08,1.2,0.08])
    ax_head=fig.add_subplot(gs[0]); ax_main=fig.add_subplot(gs[1]); ax_sep=fig.add_subplot(gs[2]); ax_vol=fig.add_subplot(gs[3])
    for ax in [ax_head,ax_main,ax_sep,ax_vol]:
        ax.set_facecolor(BG_PANEL); ax.tick_params(colors=TEXT_COL,labelsize=8)
        for spine in ax.spines.values(): spine.set_edgecolor(GRID_COL)
    ax_head.set_xlim(0,1); ax_head.set_ylim(0,1); ax_head.axis("off"); ax_head.set_facecolor(BG_TOP)
    ax_head.text(0.012,0.72,f"{sym}/USDT",color=WHITE,fontsize=14,fontweight="bold",va="center",transform=ax_head.transAxes)
    ax_head.text(0.012,0.25,iv_label,color=TEXT_COL,fontsize=9,va="center",transform=ax_head.transAxes)
    ax_head.text(0.18,0.72,f"{price_now:,.4f}",color=chg_col,fontsize=13,fontweight="bold",va="center",transform=ax_head.transAxes)
    ax_head.text(0.18,0.25,f"{chg_sign}{price_chg:.2f}%",color=chg_col,fontsize=9,va="center",transform=ax_head.transAxes)
    stats=[("O",f"{o[-1]:,.4f}"),("H",f"{h[-1]:,.4f}"),("L",f"{l[-1]:,.4f}"),("C",f"{c[-1]:,.4f}")]
    for i,(lbl,val) in enumerate(stats):
        xpos=0.36+i*0.095
        ax_head.text(xpos,0.72,lbl,color=TEXT_COL,fontsize=7.5,va="center",transform=ax_head.transAxes)
        ax_head.text(xpos,0.25,val,color=WHITE,fontsize=7.5,va="center",transform=ax_head.transAxes)
    ma_items=[(f"MA(7)  {ma7[-1]:,.2f}",MA7_COL),(f"MA(25) {ma25[-1]:,.2f}" if not pd.isna(ma25[-1]) else "MA(25) —",MA25_COL),(f"MA(99) {ma99[-1]:,.2f}" if not pd.isna(ma99[-1]) else "MA(99) —",MA99_COL)]
    for i,(txt,col) in enumerate(ma_items): ax_head.text(0.72+i*0.093,0.5,txt,color=col,fontsize=7.5,va="center",transform=ax_head.transAxes)
    ax_main.set_xlim(-1,len(df)); ax_main.set_facecolor(BG_PANEL); ax_main.grid(axis="both",color=GRID_COL,linewidth=0.4,linestyle="-")
    ax_main.tick_params(axis="x",labelbottom=False); ax_main.yaxis.set_label_position("right"); ax_main.yaxis.tick_right()
    candle_w=0.6
    for i in x:
        bull=c[i]>=o[i]; fc=GREEN if bull else RED
        ax_main.plot([i,i],[l[i],h[i]],color=fc,linewidth=0.8,zorder=2)
        body_lo=min(o[i],c[i]); body_hi=max(o[i],c[i]); height=max(body_hi-body_lo,(h[i]-l[i])*0.003)
        ax_main.add_patch(plt.Rectangle((i-candle_w/2,body_lo),candle_w,height,color=fc,zorder=3))
    xi=list(x)
    ax_main.plot(xi,ma7,color=MA7_COL,linewidth=1.0,zorder=4)
    ax_main.plot(xi,ma25,color=MA25_COL,linewidth=1.0,zorder=4)
    ax_main.plot(xi,ma99,color=MA99_COL,linewidth=1.0,zorder=4)
    ax_main.axhline(price_now,color=chg_col,linewidth=0.7,linestyle="--",alpha=0.7,zorder=5)
    ax_main.text(len(df)-0.3,price_now,f" {price_now:,.4f}",color=chg_col,fontsize=7.5,va="center",zorder=6)
    step=max(1,len(df)//8); tick_pos=list(range(0,len(df),step))
    tick_lbls=[df["dt"].iloc[i].strftime("%m/%d %H:%M") for i in tick_pos]
    ax_main.set_xticks(tick_pos); ax_main.set_xticklabels(tick_lbls,fontsize=6.5,color=TEXT_COL,rotation=0); ax_main.tick_params(axis="x",labelbottom=True)
    pad=(h.max()-l.min())*0.04; ax_main.set_ylim(l.min()-pad,h.max()+pad)
    ax_sep.axis("off"); ax_sep.set_facecolor(BG_MAIN)
    ax_vol.set_xlim(-1,len(df)); ax_vol.set_facecolor(BG_PANEL); ax_vol.grid(axis="y",color=GRID_COL,linewidth=0.3)
    ax_vol.yaxis.set_label_position("right"); ax_vol.yaxis.tick_right(); ax_vol.tick_params(axis="x",labelbottom=False)
    for i in x:
        vcol=VOL_GREEN if c[i]>=o[i] else VOL_RED; ax_vol.bar(i,v[i],width=candle_w,color=vcol,zorder=2)
    ax_vol.set_ylabel("Vol",color=TEXT_COL,fontsize=7.5,labelpad=2)
    vol_ma=pd.Series(v).rolling(10).mean().values; ax_vol.plot(xi,vol_ma,color=MA25_COL,linewidth=0.8,zorder=3)
    ax_main.text(0.5,0.5,f"{sym}/USDT",transform=ax_main.transAxes,fontsize=28,color="#FFFFFF",alpha=0.03,ha="center",va="center",fontweight="bold")
    plt.tight_layout(pad=0)
    fname=os.path.join(CHART_DIR,f"chart_{sym}_{iv}_{int(datetime.now().timestamp())}.png")
    fig.savefig(fname,dpi=150,bbox_inches="tight",facecolor=BG_MAIN,edgecolor="none"); plt.close(fig)
    return {"status":"success","path":os.path.abspath(fname),"symbol":sym,"interval":iv_label,"price":price_now,"change":f"{chg_sign}{price_chg:.2f}%"}


# ─────────────────────────────────────────────
#  PER-USER HISTORY (SQLite-backed)
# ─────────────────────────────────────────────
class UserHistory:
    def __init__(self, user_id):
        self.user_id  = user_id
        self.messages = self._load()

    def _load(self):
        try:
            return database.history_get(str(self.user_id))
        except Exception:
            return []

    def _save(self):
        try:
            database.history_save(str(self.user_id), self.messages)
        except Exception as e:
            print(f"[History] Save error: {e}")

    def add(self, role, content):
        self.messages.append({"timestamp":datetime.now().isoformat(),"role":role,"content":content})
        if len(self.messages) > MAX_HISTORY*2: self.messages = self.messages[-(MAX_HISTORY*2):]
        self._save()

    def get_api_messages(self):
        msgs = [{"role":"system","content":SYSTEM_PROMPT}]
        relevant = [m for m in self.messages if m["role"] in ("user","assistant")]
        for m in relevant[-MAX_HISTORY:]: msgs.append({"role":m["role"],"content":m["content"]})
        return msgs

    def clear(self):
        self.messages = []; self._save()

    def summary(self):
        total=len(self.messages); user_c=sum(1 for m in self.messages if m["role"]=="user"); ai_c=sum(1 for m in self.messages if m["role"]=="assistant")
        if not self.messages: return "No history yet."
        return f"Total: {total} | You: {user_c} | AI: {ai_c} | Since: {self.messages[0]['timestamp'][:10]}"


# ─────────────────────────────────────────────
#  TOOL EXECUTOR — uses cached prices where possible
# ─────────────────────────────────────────────
class ToolExecutor:
    _crypto_tools      = None
    _market_summary    = None
    _last_pending_trade = None
    _current_user_id   = None

    @classmethod
    def _ct(cls):
        if cls._crypto_tools is None:
            from crypto_tools import CryptoTools
            cls._crypto_tools = CryptoTools()
        return cls._crypto_tools

    @classmethod
    def _ms(cls):
        if cls._market_summary is None:
            from market_summary_agent import MarketSummaryAgent
            cls._market_summary = MarketSummaryAgent()
        return cls._market_summary

    @classmethod
    def run(cls, name, args):
        try:
            if   name == "get_crypto_history":           return cls._ct().get_crypto_history(args.get("symbol"))
            elif name == "get_latest_news":              return cls._ct().get_latest_news(symbol=args.get("symbol"), limit=int(args.get("limit", 5)))
            elif name == "fetch_and_analyze_asset_news": return cls._ct().fetch_and_analyze_asset_news(args.get("symbol"))
            elif name == "get_technical_analysis":       return cls._ct().get_technical_analysis(args.get("symbol"))
            elif name == "get_all_technicals":           return cls._ct().get_all_technicals()
            elif name == "get_current_points":           return cls._ct().get_current_points(args.get("symbol"))
            elif name == "get_whale_data":               return cls._ct().get_whale_data()
            elif name == "get_live_price":
                sym    = args.get("symbol", "BTC")
                cached = get_cached_price(sym)
                if cached:
                    return {"symbol": sym.upper().replace("USDT","")+"USDT",
                            "price": cached["price"],
                            "change24h": cached.get("change24h", 0),
                            "volume": cached.get("volume", 0),
                            "source": "bitget_live"}
                return cls._ct().get_live_price(sym)
            elif name == "get_top_gainers":
                limit = int(args.get("limit", 5))
                with _price_cache_lock:
                    cached_gainers = _market_cache.get("top_gainers", [])
                if cached_gainers:
                    return cached_gainers[:limit]
                return cls._ct().get_top_gainers(limit)
            elif name == "get_market_summary":
                summary = cls._ct().get_market_summary()
                live_prices_injected = {}
                with _price_cache_lock:
                    for sym, v in _price_cache.items():
                        coin = sym.replace("USDT", "")
                        live_prices_injected[coin] = {
                            "price":      v["price"],
                            "change_24h": v.get("change24h", 0),
                            "high24h":    v.get("high24h", 0),
                            "low24h":     v.get("low24h", 0),
                        }
                if live_prices_injected:
                    summary["live_prices"] = live_prices_injected
                return summary
            elif name == "generate_chart":
                sym_raw  = args.get("symbol","BTC")
                interval = args.get("interval","4h")
                limit    = int(args.get("limit",80))
                sym      = sym_raw.upper().replace("USDT","")
                try:
                    df = _fetch_ohlcv(sym, interval, limit)
                    if df is None or df.empty:
                        return {"status":"error","message":f"Could not fetch chart data for {sym}"}
                    candles = []
                    for _, row in df.iterrows():
                        candles.append({"t":int(row["open_time"]),"o":float(row["open"]),"h":float(row["high"]),"l":float(row["low"]),"c":float(row["close"]),"v":float(row["volume"])})
                    live = get_cached_price(sym)
                    if live and live.get("price"):
                        price     = live["price"]
                        change24h = live.get("change24h", 0)
                        chg_str   = f"{change24h:+.2f}%"
                    else:
                        price   = candles[-1]["c"] if candles else 0
                        chg     = (candles[-1]["c"]-candles[0]["o"])/candles[0]["o"]*100 if candles and candles[0]["o"] else 0
                        chg_str = f"{chg:+.2f}%"
                    return {"status":"success","chart_data":True,"symbol":sym,"interval":interval,"price":round(price,6),"change":chg_str,"candles":candles}
                except Exception as e:
                    return {"status":"error","message":str(e)}
            elif name == "get_trade_setup":
                from trade_setup_agent import get_trade_setup
                return get_trade_setup(symbol=args.get("symbol","BTC"),timeframe=args.get("timeframe","4h"))
            elif name == "propose_trade":
                raw_price = args.get("price")
                raw_el    = args.get("entry_low")
                raw_eh    = args.get("entry_high")
                pending = trade_manager.create_pending(
                    user_id     = getattr(cls, "_current_user_id", "unknown"),
                    symbol      = args.get("symbol", "BTCUSDT"),
                    side        = args.get("side", "buy"),
                    size        = float(args.get("size", 0)),
                    price       = float(raw_price) if raw_price else None,
                    entry_low   = float(raw_el) if raw_el else None,
                    entry_high  = float(raw_eh) if raw_eh else None,
                    sl          = float(args.get("sl")) if args.get("sl") else None,
                    tp          = float(args.get("tp")) if args.get("tp") else None,
                    reason      = args.get("reason", ""),
                )
                cls._last_pending_trade = pending
                return {"status": "pending_confirmation", "trade_id": pending["trade_id"],
                        "message": "Trade proposal sent to user for confirmation. Awaiting response."}
            elif name == "get_user_trade_history":
                import demo_trading as _dt
                import time as _time
                from datetime import datetime as _datetime
                limit = int(args.get("limit", 10))
                with _price_cache_lock:
                    prices = {sym.replace("USDT",""): {"price": v["price"]} for sym, v in _price_cache.items()}
                acc = _dt.get_positions_with_pnl(str(getattr(cls, "_current_user_id", "")), prices)
                now = _time.time()
                positions = acc.get("positions", [])
                for pos in positions:
                    opened_str = pos.get("opened_at", "")
                    if opened_str:
                        try:
                            opened_ts = _datetime.fromisoformat(opened_str).timestamp()
                            age_h = (now - opened_ts) / 3600
                        except Exception:
                            age_h = 0
                        pos["age_hours"] = round(age_h, 1)
                        pos["age_label"] = f"{int(age_h)}h {int((age_h%1)*60)}m" if age_h < 48 else f"{age_h/24:.1f}d"
                history_trades = acc.get("trade_history", [])[-limit:]
                return {
                    "status":          "success",
                    "mode":            "demo",
                    "balance_usdt":    acc.get("balance_usdt"),
                    "equity":          acc.get("equity"),
                    "total_pnl":       acc.get("total_pnl"),
                    "unrealised_pnl":  acc.get("unrealised_pnl"),
                    "win_rate":        acc.get("win_rate"),
                    "total_trades":    acc.get("total_trades"),
                    "open_count":      len(positions),
                    "open_positions":  positions,
                    "closed_trades":   history_trades,
                }
            elif name == "analyze_trade_aging":
                import demo_trading as _dt
                import time as _time
                from datetime import datetime as _datetime
                with _price_cache_lock:
                    prices = {sym.replace("USDT",""): {"price": v["price"]} for sym, v in _price_cache.items()}
                acc = _dt.get_positions_with_pnl(str(getattr(cls, "_current_user_id", "")), prices)
                now = _time.time()
                aging_report = []
                for pos in acc.get("positions", []):
                    opened_str = pos.get("opened_at", "")
                    try:
                        age_h = (now - _datetime.fromisoformat(opened_str).timestamp()) / 3600 if opened_str else 0
                    except Exception:
                        age_h = 0
                    pnl    = pos.get("unrealised_pnl", 0)
                    sym_c  = pos.get("symbol","").replace("/USDT","").replace("USDT","")
                    status_label = "CRITICAL" if age_h > 72 else "AGING" if age_h > 24 else "NORMAL"
                    aging_report.append({
                        "symbol":        sym_c,
                        "side":          pos.get("side",""),
                        "age_hours":     round(age_h, 1),
                        "age_label":     f"{int(age_h)}h" if age_h < 48 else f"{age_h/24:.1f}d",
                        "unrealised_pnl":round(pnl, 2),
                        "entry_price":   pos.get("entry_price"),
                        "sl":            pos.get("sl"),
                        "tp":            pos.get("tp"),
                        "status":        status_label,
                        "trade_id":      pos.get("trade_id",""),
                    })
                aging_report.sort(key=lambda x: x["age_hours"], reverse=True)
                critical = [p for p in aging_report if p["status"] == "CRITICAL"]
                aging    = [p for p in aging_report if p["status"] == "AGING"]
                return {
                    "status":          "success",
                    "total_open":      len(aging_report),
                    "critical_count":  len(critical),
                    "aging_count":     len(aging),
                    "positions":       aging_report,
                    "recommendation":  (
                        "CRITICAL: Review and consider closing positions open 72h+ to manage risk"
                        if critical else
                        "Some positions aging — monitor closely" if aging else
                        "No open positions" if not aging_report else "Positions within normal aging range"
                    ),
                }
            elif name == "get_risk_status":
                import demo_trading as _dt
                with _price_cache_lock:
                    prices = {sym.replace("USDT",""): {"price": v["price"]} for sym, v in _price_cache.items()}
                acc      = _dt.get_positions_with_pnl(str(getattr(cls, "_current_user_id", "")), prices)
                balance  = acc.get("balance_usdt", 10000)
                equity   = acc.get("equity", balance)
                positions = acc.get("positions", [])
                total_exp = sum(p.get("size_usdt", 0) for p in positions)
                exp_pct   = (total_exp / equity * 100) if equity > 0 else 0
                drawdown  = max(0, (balance - equity) / balance * 100) if balance > 0 else 0
                try:
                    from crypto_tools import load_market_summary_db as _lms
                    summary_d = _lms()
                    danger    = summary_d.get("danger_level", {})
                except Exception:
                    danger = {}
                danger_label = danger.get("label", "UNKNOWN")
                danger_score = danger.get("score", 0)
                risk_score   = min(100, int(exp_pct * 0.5 + drawdown * 2 + danger_score * 5))
                return {
                    "status":                "success",
                    "balance_usdt":          round(balance, 2),
                    "equity":                round(equity, 2),
                    "total_pnl":             round(acc.get("total_pnl", 0), 2),
                    "unrealised_pnl":        round(acc.get("unrealised_pnl", 0), 2),
                    "open_positions":        len(positions),
                    "total_exposure_usdt":   round(total_exp, 2),
                    "exposure_pct":          round(exp_pct, 1),
                    "drawdown_pct":          round(drawdown, 1),
                    "win_rate":              acc.get("win_rate", 0),
                    "market_danger":         danger_label,
                    "danger_score":          danger_score,
                    "risk_score":            risk_score,
                    "safe_to_trade":         risk_score < 60 and len(positions) < 5,
                    "max_new_position_usdt": round(min(equity * 0.2, max(0, equity - total_exp)), 2),
                }
            else: return {"error":f"Unknown function: {name}"}
        except Exception as e: return {"error":str(e)}


# ─────────────────────────────────────────────
#  IN-MEMORY STATE
# ─────────────────────────────────────────────
_user_histories: dict = {}
_user_locks:     dict = {}


def get_history(user_id):
    if user_id not in _user_histories: _user_histories[user_id] = UserHistory(user_id)
    return _user_histories[user_id]


def get_lock(user_id):
    if user_id not in _user_locks: _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def build_tool_status(log, extra=None):
    steps = []
    for entry in log:
        icon   = TOOL_ICONS.get(entry["name"], "🔧")
        args_s = ", ".join(f"{k}={v}" for k,v in entry["args"].items()) or "—"
        steps.append({
            "name":   entry["name"],
            "icon":   icon,
            "label":  entry["name"].replace("_", " "),
            "args":   args_s,
            "status": entry.get("status", "⏳"),
        })
    result = {"steps": steps}
    if extra:
        result["extra"] = extra
    return json.dumps(result)


def strip_thinking(text):
    if not text: return ""
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<thinking>[\s\S]*?</thinking>', '', text, flags=re.IGNORECASE)
    return text.strip()


def strip_markdown(text):
    if not text: return ""
    text = strip_thinking(text)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text, flags=re.DOTALL)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__',     r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'\*([^*\n]+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_\n]+?)_',   r'<i>\1</i>', text)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'^\s*#{1,6}\s+(.+)', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*•]\s+(.+)', r'• \1', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+(.+)', r'• \1', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_xml_tool_calls(content):
    FUNC_ALIASES = {
        "proposetrade": "propose_trade", "propose_trade": "propose_trade",
        "generatechart": "generate_chart", "generate_chart": "generate_chart",
        "getliveprice": "get_live_price", "getlatestNews": "get_latest_news",
        "gettechnicalanalysis": "get_technical_analysis",
        "getmarketsummary": "get_market_summary",
        "getwhaledata": "get_whale_data",
        "gettradesetup": "get_trade_setup",
    }
    PARAM_ALIASES = {
        "stoploss": "sl", "stop_loss": "sl",
        "takeprofit": "tp", "take_profit": "tp",
        "size_usdt": "size", "amount": "size",
    }
    tool_calls = []
    pattern = r"<tool_?call>\s*<function=(\w+)>(.*?)</function>\s*</tool_?call>"
    for i, m in enumerate(re.finditer(pattern, content, re.DOTALL | re.IGNORECASE)):
        raw_name  = m.group(1).lower()
        func_name = FUNC_ALIASES.get(raw_name, raw_name)
        params_raw = m.group(2)
        params = {}
        for pm in re.finditer(r"<parameter=(\w+)>(.*?)</parameter>", params_raw, re.DOTALL):
            key = pm.group(1).lower()
            key = PARAM_ALIASES.get(key, key)
            params[key] = pm.group(2).strip()
        tool_calls.append({"id": f"call_{i}", "type": "function",
                           "function": {"name": func_name, "arguments": json.dumps(params)}})
    return tool_calls


# ─────────────────────────────────────────────
#  WS STATUS ADAPTER
# ─────────────────────────────────────────────
class WsStatusMsg:
    def __init__(self, websocket):
        self.ws = websocket

    async def edit_text(self, text, **kwargs):
        try:
            await self.ws.send_json({"type": "status", "content": text})
        except Exception:
            pass

    async def delete(self):
        try:
            await self.ws.send_json({"type": "status", "content": ""})
        except Exception:
            pass


async def safe_edit(msg, text, **kwargs):
    await msg.edit_text(text)
    await asyncio.sleep(0.05)


# ─────────────────────────────────────────────
#  CORE CHAT
# ─────────────────────────────────────────────
async def run_chat(user_id, user_input, status_msg):
    history              = get_history(user_id)
    chart_data_list      = []
    trade_setup_response = None
    ToolExecutor._current_user_id    = str(user_id)
    ToolExecutor._last_pending_trade = None

    history.add("user", user_input)
    messages = history.get_api_messages()
    messages.append({"role": "user", "content": user_input})

    headers  = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
    loop     = asyncio.get_event_loop()
    tool_log = []
    ai_response = ""

    MAX_TURNS = 6
    for _turn in range(MAX_TURNS):
        payload  = {"model": MODEL, "messages": messages, "tools": TOOLS_SCHEMA,
                    "tool_choice": "auto", "temperature": 0.1, "max_tokens": 2000}
        response = await loop.run_in_executor(None, lambda: requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=120))
        result   = response.json()

        if "error" in result:
            raise RuntimeError(result["error"].get("message", "Unknown NVIDIA API error"))

        message = result["choices"][0]["message"]
        content = strip_thinking(message.get("content") or "")

        if not message.get("tool_calls") and ("<tool_call>" in content or "<toolcall>" in content):
            xml_calls = parse_xml_tool_calls(content)
            if xml_calls:
                message["tool_calls"] = xml_calls
                content = ""

        if message.get("tool_calls"):
            messages.append(message)

            for tool_call in message["tool_calls"]:
                fn_name = tool_call["function"]["name"]
                fn_args = json.loads(tool_call["function"]["arguments"])

                tool_log.append({"name": fn_name, "args": fn_args, "status": "⏳"})
                await safe_edit(status_msg, build_tool_status(tool_log))

                fn_response = await loop.run_in_executor(None, lambda n=fn_name, a=fn_args: ToolExecutor.run(n, a))

                if fn_name == "generate_chart" and isinstance(fn_response, dict) and fn_response.get("chart_data"):
                    chart_data_list.append(fn_response)

                if fn_name == "get_trade_setup" and isinstance(fn_response, dict):
                    if fn_response.get("error") and not fn_response.get("text"):
                        trade_setup_response = f"❌ Trade Setup Error\n{fn_response['error']}"
                    else:
                        from trade_setup_agent import format_trade_setup_message
                        await safe_edit(status_msg, build_tool_status(tool_log, extra="🤖 AI is analysing news + signals → generating setup…"))
                        raw_text = format_trade_setup_message(fn_response)
                        trade_setup_response = strip_thinking(raw_text)

                tool_log[-1]["status"] = "✅"
                await safe_edit(status_msg, build_tool_status(tool_log))
                messages.append({"tool_call_id": tool_call["id"], "role": "tool", "name": fn_name,
                                  "content": json.dumps(fn_response, ensure_ascii=False, default=str)})

            continue

        else:
            ai_response = content
            break

    await safe_edit(status_msg, build_tool_status(tool_log, extra="✍️ Generating response…") if tool_log else "")

    ai_response = strip_markdown(ai_response)

    if not ai_response or not ai_response.strip():
        if trade_setup_response:
            ai_response = ""
        else:
            ai_response = "I have retrieved the latest data. Ask a specific question about any coin or trading setup for a detailed analysis."

    history.add("assistant", trade_setup_response if trade_setup_response and not ai_response else ai_response)
    pending_trade = ToolExecutor._last_pending_trade
    ToolExecutor._last_pending_trade = None
    return ai_response, chart_data_list, trade_setup_response, pending_trade


# ─────────────────────────────────────────────
#  FASTAPI
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    start_background_agents()
    asyncio.create_task(_dash_broadcaster())
    asyncio.create_task(_trade_cleanup_loop())
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT_DIR, "public", "static")), name="static")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    from ws_dispatcher import WsSession, dispatch

    session = WsSession(websocket)

    async def _subscribe_dash_for_session(ws):
        snap = _get_memory_snapshot()
        try: await ws.send_json(snap)
        except Exception: pass
        tick = _build_price_tick()
        try: await ws.send_json(tick)
        except Exception: pass
        _dash_clients.add(ws)

    try:
        while True:
            raw = await websocket.receive_text()
            await dispatch(
                session             = session,
                raw                 = raw,
                get_history_fn      = get_history,
                lock_fn             = get_lock,
                run_chat_fn         = run_chat,
                broadcast_dash_subscribe_fn = _subscribe_dash_for_session,
            )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] Error: {e}")
    finally:
        _dash_clients.discard(websocket)


@app.get("/")
async def get():
    with open(os.path.join(ROOT_DIR, "public", "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ─────────────────────────────────────────────
#  DASHBOARD WS  /ws-dash
# ─────────────────────────────────────────────
_dash_clients: set = set()
_main_loop: "asyncio.AbstractEventLoop | None" = None
_agent_status: dict = {
    "news":           {"name": "News Agent",           "status": "starting", "last_run": None, "runs": 0, "errors": 0},
    "technical":      {"name": "Technical Agent",      "status": "starting", "last_run": None, "runs": 0, "errors": 0},
    "whale":          {"name": "Whale Agent",           "status": "starting", "last_run": None, "runs": 0, "errors": 0},
    "market_summary": {"name": "Market Summary Agent", "status": "starting", "last_run": None, "runs": 0, "errors": 0},
}


async def _push_dash(ws, msg: dict):
    try:
        await ws.send_json(msg)
    except Exception:
        pass


async def _broadcast_dash(msg: dict):
    global _dash_clients
    dead = set()
    for ws in list(_dash_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _dash_clients -= dead


def _build_price_tick() -> dict:
    with _price_cache_lock:
        prices = {
            sym.replace("USDT", ""): {
                "price":     v["price"],
                "change24h": v.get("change24h", 0),
                "volume":    v.get("volume", 0),
                "high24h":   v.get("high24h", 0),
                "low24h":    v.get("low24h", 0),
                "ts":        v.get("ts", ""),
            }
            for sym, v in _price_cache.items()
        }
        gainers   = _market_cache.get("top_gainers", [])
        total_vol = _market_cache.get("total_volume", 0)
    return {
        "type":         "price_tick",
        "prices":       prices,
        "top_gainers":  gainers[:5],
        "total_volume": total_vol,
        "ts":           datetime.now().isoformat(),
    }


def _get_memory_snapshot() -> dict:
    """Full memory snapshot for dashboard — reads from SQLite via database.kv_get."""
    try:
        news_db    = database.kv_get("news")
        tech_db    = database.kv_get("technical")
        whale_db   = database.kv_get("whale")
        summary_db = database.kv_get("market_summary")

        news_items = []
        for item in (news_db.get("news") or [])[-8:]:
            news_items.append({
                "title":     str(item.get("title", ""))[:80],
                "source":    item.get("source", ""),
                "sentiment": item.get("sentiment", item.get("analysis", {}).get("sentiment", "—")),
                "impact":    item.get("impact",    item.get("analysis", {}).get("impact", "—")),
                "assets":    ", ".join(item.get("affected_coins", [])) or item.get("analysis", {}).get("affected_assets", "—"),
                "summary":   str(item.get("summary", item.get("analysis", {}).get("summary", "")))[:120],
            })

        tech = {}
        for coin, snap in (tech_db.get("snapshots") or {}).items():
            sigs = snap.get("signals", {})
            tech[coin] = {
                "signal":     snap.get("consensus", "NEUTRAL"),
                "score":      snap.get("up_score", 0),
                "rsi":        (sigs.get("RSI") or {}).get("signal", "—"),
                "macd":       (sigs.get("MACD") or {}).get("signal", "—"),
                "ema":        (sigs.get("EMA_Alignment") or {}).get("signal", "—"),
                "bb":         (sigs.get("Bollinger_Bands") or {}).get("signal", "—"),
                "stoch":      (sigs.get("Stochastic") or {}).get("signal", "—"),
                "buy_count":  snap.get("buy_count", 0),
                "sell_count": snap.get("sell_count", 0),
                "pct_buy":    round(snap.get("pct_buy", 0), 1),
                "pct_sell":   round(snap.get("pct_sell", 0), 1),
            }

        whale_items = []
        for w in (whale_db.get("alerts") or [])[-6:]:
            if isinstance(w, dict):
                text = w.get("text") or w.get("description") or w.get("raw", "")
                whale_items.append({"text": str(text)[:100], "ts": str(w.get("timestamp", w.get("ts", "")))})
            elif isinstance(w, str):
                whale_items.append({"text": w[:100], "ts": ""})

        scores = {}
        for coin, cmp in (summary_db.get("coin_master_points") or {}).items():
            scores[coin] = {
                "score":  cmp.get("sentiment_pct", 50.0),
                "reason": cmp.get("sentiment", "NEUTRAL"),
                "date":   summary_db.get("last_updated", ""),
            }

        with _price_cache_lock:
            btc_live  = _price_cache.get("BTCUSDT", {})
            eth_live  = _price_cache.get("ETHUSDT", {})
            gainers_c = _market_cache.get("top_gainers", [])
            total_vol = _market_cache.get("total_volume", 0)

        ai_summary = summary_db.get("ai_summary", {})
        master_pts = summary_db.get("master_points", {})
        danger     = whale_db.get("danger_level", {})

        market: dict = {}
        market["sentiment"]      = master_pts.get("sentiment", "NEUTRAL")
        market["sentiment_pct"]  = master_pts.get("sentiment_pct", 50.0)
        market["action"]         = ai_summary.get("action", "WAIT")
        market["danger_level"]   = danger.get("label", "UNKNOWN")
        market["danger_score"]   = danger.get("score", 0)

        if btc_live.get("price"):
            market["btc_price"]  = f"${btc_live['price']:,.2f}"
            market["btc_change"] = f"{btc_live.get('change24h', 0):+.2f}%"
            market["btc_volume"] = f"${btc_live.get('volume', 0)/1e6:.1f}M"
        elif summary_db.get("live_prices", {}).get("BTC", {}).get("price"):
            market["btc_price"]  = f"${summary_db['live_prices']['BTC']['price']:,.2f}"

        if eth_live.get("price"):
            market["eth_price"]  = f"${eth_live['price']:,.2f}"

        if total_vol:
            market["total_volume"] = f"${total_vol/1e9:.2f}B"

        if gainers_c:
            top = gainers_c[0]
            market["top_gainer"] = f"{top['symbol']} {top['change']:+.2f}%"

        news_coin_pts  = news_db.get("coin_points", {})
        news_total_pts = news_db.get("total_points", {"up": 0, "down": 0, "total": 0})

        return {
            "type":         "memory_snapshot",
            "last_updated": datetime.now().isoformat(),
            "market":       market,
            "news":         list(reversed(news_items)),
            "technical":    tech,
            "whale_alerts": list(reversed(whale_items)),
            "scores":       scores,
            "news_points":  news_coin_pts,
            "news_total":   news_total_pts,
            "agents":       dict(_agent_status),
        }
    except Exception as e:
        print(f"[Snapshot] Error: {e}")
        return {"type": "memory_snapshot", "error": str(e), "agents": dict(_agent_status)}


async def _trade_cleanup_loop():
    while True:
        await asyncio.sleep(60)
        try:
            trade_manager.cleanup_expired_all()
        except Exception as e:
            print(f"[TradeCleanup] Error: {e}")


async def _dash_broadcaster():
    tick_count = 0
    while True:
        await asyncio.sleep(5)
        if not _dash_clients:
            continue
        tick_count += 1
        tick = _build_price_tick()
        await _broadcast_dash(tick)
        if tick_count % 6 == 0:
            snap = _get_memory_snapshot()
            await _broadcast_dash(snap)


@app.websocket("/ws-dash")
async def dashboard_ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _dash_clients.add(websocket)

    snap = _get_memory_snapshot()
    await _push_dash(websocket, snap)
    tick = _build_price_tick()
    await _push_dash(websocket, tick)

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                if data == "ping":
                    await _push_dash(websocket, {"type": "pong"})
            except asyncio.TimeoutError:
                await _push_dash(websocket, _build_price_tick())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _dash_clients.discard(websocket)


# ─────────────────────────────────────────────
#  AGENT STATUS NOTIFY
# ─────────────────────────────────────────────
def _notify_agent(key: str, status: str, error: bool = False):
    _agent_status[key]["status"]   = status
    _agent_status[key]["last_run"] = datetime.now().isoformat()
    if status == "running":
        _agent_status[key]["runs"] += 1
    if error:
        _agent_status[key]["errors"] += 1
    if _main_loop and _main_loop.is_running():
        snap = _get_memory_snapshot()
        asyncio.run_coroutine_threadsafe(_broadcast_dash(snap), _main_loop)


# ─────────────────────────────────────────────
#  BACKGROUND AGENT LOOPS
# ─────────────────────────────────────────────
def _news_loop():
    import crypto_news_reader
    print("[NewsAgent] Started")
    while True:
        _notify_agent("news", "running")
        saved_count = 0
        try:
            processed = crypto_news_reader.load_processed_links()

            for name, url in crypto_news_reader.FEEDS.items():
                try:
                    articles = crypto_news_reader.get_latest_news_feed(name, url)
                except Exception as e:
                    print(f"[NewsAgent] Feed error ({name}): {e}")
                    continue

                if not articles:
                    continue

                new_articles = [a for a in articles[:5] if getattr(a, "link", "") not in processed][:3]

                for article in new_articles:
                    link  = getattr(article, "link",  "")
                    title = getattr(article, "title", "No title")
                    if not link:
                        continue

                    crypto_news_reader.save_processed_link(link)
                    processed.add(link)

                    try:
                        text = crypto_news_reader.get_article_text(link) or \
                               getattr(article, "description", "No content")

                        result = crypto_news_reader.analyze_and_save_news(
                            title=title, content=text, link=link, source=name,
                        )

                        if result:
                            saved_count += 1
                            print(f"[NewsAgent] Saved: {title[:60]}…")

                    except Exception as e:
                        print(f"[NewsAgent] Article error ({name}): {e}")
                        continue

            print(f"[NewsAgent] Cycle done — {saved_count} new articles saved")
            _notify_agent("news", "idle")

        except Exception as e:
            print(f"[NewsAgent] Cycle error: {e}")
            _notify_agent("news", "error", error=True)

        _time.sleep(300)


def _tech_loop():
    import crypto_technical_analysis
    from sub_agents_runner import (
        load_technical_db, save_technical_db,
        calculate_up_down_score, update_points_on_snapshot, build_snapshot
    )
    print("[TechAgent] Started")
    while True:
        _notify_agent("technical", "running")
        updated = 0
        try:
            db = load_technical_db()

            for symbol in crypto_technical_analysis.SYMBOLS:
                try:
                    df = crypto_technical_analysis.fetch_ohlcv(symbol)
                    if df is None or df.empty:
                        print(f"[TechAgent] No data for {symbol}")
                        continue

                    price = crypto_technical_analysis.fetch_price(symbol)
                    if not price:
                        price = float(df.iloc[-1]["close"])

                    engine = crypto_technical_analysis.SignalEngine(df)
                    result = engine.get_signals()

                    up_score, down_score = calculate_up_down_score(result)
                    coin = symbol.replace("USDT", "")

                    snapshot = build_snapshot(coin, price, result, up_score, down_score)
                    update_points_on_snapshot(db, coin, up_score, down_score)
                    db["snapshots"][coin] = snapshot

                    print(f"[TechAgent] {coin}: {result.get('consensus','?')} | up={up_score} down={down_score}")
                    updated += 1

                except Exception as e:
                    print(f"[TechAgent] Error for {symbol}: {e}")
                    continue

            db["last_updated"] = datetime.now().isoformat()
            save_technical_db(db)
            print(f"[TechAgent] Cycle done — {updated}/{len(crypto_technical_analysis.SYMBOLS)} saved")
            _notify_agent("technical", "idle")

        except Exception as e:
            print(f"[TechAgent] Cycle error: {e}")
            _notify_agent("technical", "error", error=True)
        _time.sleep(30)


def _whale_loop():
    import whale_alert_monitor
    print("[WhaleAgent] Started")
    while True:
        _notify_agent("whale", "running")
        try:
            raw_data = whale_alert_monitor.fetch_data()

            if raw_data:
                danger     = whale_alert_monitor.calculate_danger_level(raw_data)
                up_score, down_score = whale_alert_monitor.calculate_up_down_from_danger(danger["score"])
                stats      = whale_alert_monitor.parse_market_stats(raw_data)

                alerts_parsed = []
                for a in raw_data.get("alerts", [])[:9]:
                    parsed = whale_alert_monitor.parse_alert_string(a)
                    parsed["id"]        = f"wh_{hash(str(a)) % 100000:05d}"
                    parsed["timestamp"] = datetime.now().isoformat()
                    parsed["raw"]       = str(a)
                    alerts_parsed.append(parsed)

                hodl_change = float(raw_data.get("hodl", {}).get("c", 0) or 0)

                db = {
                    "last_updated": datetime.now().isoformat(),
                    "danger_level": danger,
                    "total_points": {"up": up_score, "down": down_score, "total": 10},
                    "market_stats": stats,
                    "alerts":       alerts_parsed,
                    "hodl_stats":   {
                        "hodl_change": hodl_change,
                        "comment":     whale_alert_monitor.hodl_comment(hodl_change),
                    },
                }
                whale_alert_monitor.save_whale_db(db)
                print(f"[WhaleAgent] Danger: {danger['label']} ({danger['score']}/10) | up={up_score} down={down_score}")
            else:
                print("[WhaleAgent] Failed to fetch whale data")

            _notify_agent("whale", "idle")

        except Exception as e:
            print(f"[WhaleAgent] Error: {e}")
            _notify_agent("whale", "error", error=True)
        _time.sleep(60)


def _market_summary_loop():
    import market_summary_agent
    from enhanced_tools import EnhancedTools
    et = EnhancedTools()
    print("[MarketSummary] Started")
    while True:
        _notify_agent("market_summary", "running")
        try:
            master_pts      = market_summary_agent.calculate_master_points()
            coin_master_pts = market_summary_agent.calculate_coin_master_points()

            live_prices = {}
            for coin in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
                try:
                    cached = get_cached_price(coin)
                    if cached.get("price"):
                        live_prices[coin] = {
                            "price":      cached["price"],
                            "change_24h": cached.get("change24h", 0),
                        }
                    else:
                        p = et.get_live_price(coin)
                        if p.get("price"):
                            live_prices[coin] = {"price": float(str(p["price"]).replace(",", "")), "change_24h": 0}
                except Exception:
                    pass

            with _price_cache_lock:
                top_gainers = list(_market_cache.get("top_gainers", [])[:5])

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
                "whale_alerts":       whale_alerts,
            }
            ai_summary = market_summary_agent.generate_ai_summary(context)

            db = {
                "last_updated":       datetime.now().isoformat(),
                "master_points":      master_pts,
                "coin_master_points": coin_master_pts,
                "live_prices":        live_prices,
                "top_gainers":        top_gainers,
                "danger_level":       danger_level,
                "ai_summary":         ai_summary,
            }
            market_summary_agent.save_market_summary_db(db)

            print(f"[MarketSummary] {master_pts['sentiment']} ({master_pts['sentiment_pct']}%) | Danger: {danger_level['label']} | Action: {ai_summary.get('action','?')}")
            _notify_agent("market_summary", "idle")

        except Exception as e:
            print(f"[MarketSummary] Error: {e}")
            _notify_agent("market_summary", "error", error=True)
        _time.sleep(300)


# ─────────────────────────────────────────────
#  STARTUP: launch price caches + agents
# ─────────────────────────────────────────────
def start_background_agents():
    threading.Thread(target=_refresh_price_cache,  daemon=True, name="PriceFeed5s").start()
    threading.Thread(target=_refresh_market_cache, daemon=True, name="MarketFeed30s").start()
    print("  ✅ Bitget real-time price feeds started (5s / 30s)")

    try:
        threading.Thread(target=_news_loop,            daemon=True, name="NewsAgent").start()
        threading.Thread(target=_tech_loop,            daemon=True, name="TechAgent").start()
        threading.Thread(target=_whale_loop,           daemon=True, name="WhaleAgent").start()
        threading.Thread(target=_market_summary_loop,  daemon=True, name="MarketSummary").start()
        print("  ✅ Background sub-agents started (News | Technical | Whale | MarketSummary)")
    except Exception as e:
        print(f"  ⚠️  Background agents failed to start: {e}")


if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("  🚀 CRYPTO AI WEB SERVER — Real-Time Dashboard Edition")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=5000)

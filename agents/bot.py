"""
agents/bot.py — Telegram Crypto AI Bot
=======================================
Per-user chat history backed by SQLite (core/database.py).
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in [ROOT_DIR,
           os.path.join(ROOT_DIR, "agents"),
           os.path.join(ROOT_DIR, "tools"),
           os.path.join(ROOT_DIR, "core")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import json
import re
import io
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.gridspec as gridspec
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter

import database

# ─────────────────────────────────────────────
#  CONFIG (from config.py)
# ─────────────────────────────────────────────
import config as _cfg
TELEGRAM_BOT_TOKEN = _cfg.TELEGRAM_BOT_TOKEN
NVIDIA_API_KEY     = _cfg.NVIDIA_API_KEY
NVIDIA_URL         = _cfg.NVIDIA_URL
MODEL              = _cfg.MODEL
BITGET_URL         = _cfg.BITGET_BASE

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
#  SYSTEM PROMPT
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are an elite Crypto Trading AI Strategist. Always fetch real-time data before advising.

RULES:
1. Give DIRECT, ACTIONABLE advice (Buy / Sell / Hold / Wait at $X).
2. Back every decision with data from tools.
3. Be concise and professional.
4. When unsure, say so and explain why.
5. Response language: English (or match the user's language).

RESPONSE FORMAT — STRICT:
- Write ONLY plain flowing prose. One or two short paragraphs maximum.
- NEVER use markdown: no **, no __, no ##, no *, no bullet points, no numbered lists, no headers.
- NEVER use dashes or hyphens to start a line.
- Do NOT mention which tools you called or what data you fetched — just use the data naturally.
- Keep it tight: market status, recommendation, key reason. That is all.
"""

# ─────────────────────────────────────────────
#  TOOL SCHEMAS
# ─────────────────────────────────────────────
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_crypto_history",
            "description": "Get the last 30 days of daily price history for a cryptocurrency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker e.g. BTC, ETH"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_news",
            "description": "Get analyzed cryptocurrency news and sentiment from memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Optional coin filter e.g. BTC"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_and_analyze_asset_news",
            "description": "Live-fetch and AI-analyze the latest news for a specific coin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker e.g. BTC"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "Get live technical signals (RSI, MACD, EMA, etc.) for a coin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker e.g. BTC"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_points",
            "description": "Get the AI score (1-10) and reasoning for a coin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker e.g. BTC"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_price",
            "description": "Get the current live price of a cryptocurrency from Bitget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker e.g. BTC, ETH"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_gainers",
            "description": "Get the top gaining cryptocurrencies from Bitget 24h ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of results (default 5)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_summary",
            "description": "Get a full market summary: prices, top gainers, news, signals.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": (
                "Generate a Bitget-style candlestick chart image for a cryptocurrency. "
                "Use this whenever the user asks for a chart, graph, candlestick, price chart, or visual analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol e.g. BTC, ETH, SOL"
                    },
                    "interval": {
                        "type": "string",
                        "description": "Candle interval. Options: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w. Default 4h."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of candles to show (default 80, max 200)."
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_setup",
            "description": (
                "Generate a complete, structured trade setup for a cryptocurrency. "
                "Use this when the user asks for a trade setup, trading plan, entry/exit levels, "
                "trade signal, buy/sell setup, TP/SL levels, or trading strategy for a coin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol e.g. BTC, ETH, SOL, XRP"
                    },
                    "timeframe": {
                        "type": "string",
                        "description": "Analysis timeframe. Options: 15m, 30m, 1h, 4h, 1d. Default 4h."
                    }
                },
                "required": ["symbol"]
            }
        }
    }
]

# ─────────────────────────────────────────────
#  TOOL ICONS
# ─────────────────────────────────────────────
TOOL_ICONS = {
    "get_crypto_history":          "📈",
    "get_latest_news":             "📰",
    "fetch_and_analyze_asset_news":"🔍",
    "get_technical_analysis":      "⚡",
    "get_current_points":          "🎯",
    "get_live_price":              "💲",
    "get_top_gainers":             "🚀",
    "get_market_summary":          "🌍",
    "generate_chart":              "📊",
    "get_trade_setup":             "🎯",
}

# ─────────────────────────────────────────────
#  BITGET-STYLE CHART GENERATOR
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

INTERVAL_LABELS = {
    "1m":"1m","5m":"5m","15m":"15m","30m":"30m",
    "1h":"1H","4h":"4H","1d":"1D","1w":"1W"
}


def _fetch_ohlcv(symbol: str, interval: str, limit: int) -> pd.DataFrame | None:
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    granularity = _INTERVAL_MAP.get(interval.lower(), interval)
    try:
        r = requests.get(
            f"{BITGET_URL}/candles",
            params={"symbol": sym, "granularity": granularity, "limit": min(limit, 1000)},
            timeout=15
        )
        r.raise_for_status()
        raw = r.json()["data"]
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "quoteVolume", "usdtVolume"
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["dt"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms")
        return df
    except Exception as e:
        print(f"[Chart] OHLCV fetch error: {e}")
        return None


def _ma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def generate_chart(symbol: str, interval: str = "4h", limit: int = 80) -> dict:
    limit = min(int(limit), 200)
    sym   = symbol.upper().replace("USDT", "")
    iv    = interval.lower()

    df = _fetch_ohlcv(sym, iv, limit)
    if df is None or df.empty:
        return {"status": "error", "message": f"Could not fetch data for {sym}"}

    x   = range(len(df))
    o   = df["open"].values
    h   = df["high"].values
    l   = df["low"].values
    c   = df["close"].values
    v   = df["volume"].values

    ma7  = _ma(df["close"], 7).values
    ma25 = _ma(df["close"], 25).values
    ma99 = _ma(df["close"], 99).values

    price_now = c[-1]
    price_chg = (c[-1] - o[0]) / o[0] * 100
    chg_col   = GREEN if price_chg >= 0 else RED
    chg_sign  = "+" if price_chg >= 0 else ""
    iv_label  = INTERVAL_LABELS.get(iv, iv.upper())

    fig = plt.figure(figsize=(14, 8), facecolor=BG_MAIN)
    gs  = gridspec.GridSpec(5, 1, hspace=0.0, height_ratios=[0.55, 3.5, 0.08, 1.2, 0.08])

    ax_head = fig.add_subplot(gs[0])
    ax_main = fig.add_subplot(gs[1])
    ax_sep  = fig.add_subplot(gs[2])
    ax_vol  = fig.add_subplot(gs[3])

    for ax in [ax_head, ax_main, ax_sep, ax_vol]:
        ax.set_facecolor(BG_PANEL)
        ax.tick_params(colors=TEXT_COL, labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COL)

    ax_head.set_xlim(0, 1)
    ax_head.set_ylim(0, 1)
    ax_head.axis("off")
    ax_head.set_facecolor(BG_TOP)
    ax_head.text(0.012, 0.72, f"{sym}/USDT", color=WHITE, fontsize=14, fontweight="bold",
                 va="center", transform=ax_head.transAxes)
    ax_head.text(0.012, 0.25, iv_label, color=TEXT_COL, fontsize=9, va="center",
                 transform=ax_head.transAxes)
    ax_head.text(0.18, 0.72, f"{price_now:,.4f}", color=chg_col, fontsize=13, fontweight="bold",
                 va="center", transform=ax_head.transAxes)
    ax_head.text(0.18, 0.25, f"{chg_sign}{price_chg:.2f}%", color=chg_col, fontsize=9,
                 va="center", transform=ax_head.transAxes)

    stats = [("O", f"{o[-1]:,.4f}"), ("H", f"{h[-1]:,.4f}"),
             ("L", f"{l[-1]:,.4f}"), ("C", f"{c[-1]:,.4f}")]
    for i, (lbl, val) in enumerate(stats):
        xpos = 0.36 + i * 0.095
        ax_head.text(xpos, 0.72, lbl, color=TEXT_COL, fontsize=7.5, va="center",
                     transform=ax_head.transAxes)
        ax_head.text(xpos, 0.25, val, color=WHITE, fontsize=7.5, va="center",
                     transform=ax_head.transAxes)

    ma_items = [
        (f"MA(7)  {ma7[-1]:,.2f}",  MA7_COL),
        (f"MA(25) {ma25[-1]:,.2f}" if not pd.isna(ma25[-1]) else "MA(25) —", MA25_COL),
        (f"MA(99) {ma99[-1]:,.2f}" if not pd.isna(ma99[-1]) else "MA(99) —", MA99_COL),
    ]
    for i, (txt, col) in enumerate(ma_items):
        ax_head.text(0.72 + i * 0.093, 0.5, txt, color=col, fontsize=7.5, va="center",
                     transform=ax_head.transAxes)

    ax_main.set_xlim(-1, len(df))
    ax_main.set_facecolor(BG_PANEL)
    ax_main.grid(axis="both", color=GRID_COL, linewidth=0.4, linestyle="-")
    ax_main.tick_params(axis="x", labelbottom=False)
    ax_main.yaxis.set_label_position("right")
    ax_main.yaxis.tick_right()

    candle_w = 0.6
    for i in x:
        bull = c[i] >= o[i]
        fc   = GREEN if bull else RED
        ax_main.plot([i, i], [l[i], h[i]], color=fc, linewidth=0.8, zorder=2)
        body_lo = min(o[i], c[i])
        body_hi = max(o[i], c[i])
        height  = max(body_hi - body_lo, (h[i] - l[i]) * 0.003)
        ax_main.add_patch(plt.Rectangle((i - candle_w/2, body_lo), candle_w, height,
                                        color=fc, zorder=3))

    xi = list(x)
    ax_main.plot(xi, ma7,  color=MA7_COL,  linewidth=1.0, zorder=4)
    ax_main.plot(xi, ma25, color=MA25_COL, linewidth=1.0, zorder=4)
    ax_main.plot(xi, ma99, color=MA99_COL, linewidth=1.0, zorder=4)
    ax_main.axhline(price_now, color=chg_col, linewidth=0.7, linestyle="--", alpha=0.7, zorder=5)
    ax_main.text(len(df) - 0.3, price_now, f" {price_now:,.4f}",
                 color=chg_col, fontsize=7.5, va="center", zorder=6)

    step = max(1, len(df) // 8)
    tick_pos  = list(range(0, len(df), step))
    tick_lbls = [df["dt"].iloc[i].strftime("%m/%d %H:%M") for i in tick_pos]
    ax_main.set_xticks(tick_pos)
    ax_main.set_xticklabels(tick_lbls, fontsize=6.5, color=TEXT_COL, rotation=0)
    ax_main.tick_params(axis="x", labelbottom=True)

    pad = (h.max() - l.min()) * 0.04
    ax_main.set_ylim(l.min() - pad, h.max() + pad)

    ax_sep.axis("off")
    ax_sep.set_facecolor(BG_MAIN)

    ax_vol.set_xlim(-1, len(df))
    ax_vol.set_facecolor(BG_PANEL)
    ax_vol.grid(axis="y", color=GRID_COL, linewidth=0.3)
    ax_vol.yaxis.set_label_position("right")
    ax_vol.yaxis.tick_right()
    ax_vol.tick_params(axis="x", labelbottom=False)

    for i in x:
        vcol = VOL_GREEN if c[i] >= o[i] else VOL_RED
        ax_vol.bar(i, v[i], width=candle_w, color=vcol, zorder=2)

    ax_vol.set_ylabel("Vol", color=TEXT_COL, fontsize=7.5, labelpad=2)
    vol_ma = pd.Series(v).rolling(10).mean().values
    ax_vol.plot(xi, vol_ma, color=MA25_COL, linewidth=0.8, zorder=3)

    ax_main.text(0.5, 0.5, f"{sym}/USDT", transform=ax_main.transAxes,
                 fontsize=28, color="#FFFFFF", alpha=0.03, ha="center", va="center",
                 fontweight="bold")

    plt.tight_layout(pad=0)
    fname = os.path.join(CHART_DIR, f"chart_{sym}_{iv}_{int(datetime.now().timestamp())}.png")
    fig.savefig(fname, dpi=150, bbox_inches="tight", facecolor=BG_MAIN, edgecolor="none")
    plt.close(fig)

    return {
        "status":   "success",
        "path":     os.path.abspath(fname),
        "symbol":   sym,
        "interval": iv_label,
        "price":    price_now,
        "change":   f"{chg_sign}{price_chg:.2f}%"
    }


# ─────────────────────────────────────────────
#  PER-USER HISTORY MANAGER (SQLite-backed)
# ─────────────────────────────────────────────
class UserHistory:
    def __init__(self, user_id: int):
        self.user_id  = user_id
        self.messages = self._load()

    def _load(self) -> list:
        try:
            return database.history_get(str(self.user_id))
        except Exception:
            return []

    def _save(self):
        try:
            database.history_save(str(self.user_id), self.messages)
        except Exception as e:
            print(f"[History] Save error for user {self.user_id}: {e}")

    def add(self, role: str, content: str):
        self.messages.append({
            "timestamp": datetime.now().isoformat(),
            "role":      role,
            "content":   content
        })
        if len(self.messages) > MAX_HISTORY * 2:
            self.messages = self.messages[-(MAX_HISTORY * 2):]
        self._save()

    def get_api_messages(self) -> list:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        relevant = [m for m in self.messages if m["role"] in ("user", "assistant")]
        for m in relevant[-MAX_HISTORY:]:
            msgs.append({"role": m["role"], "content": m["content"]})
        return msgs

    def clear(self):
        self.messages = []
        self._save()

    def summary(self) -> str:
        total  = len(self.messages)
        user_c = sum(1 for m in self.messages if m["role"] == "user")
        ai_c   = sum(1 for m in self.messages if m["role"] == "assistant")
        if not self.messages:
            return "No history yet."
        first_ts = self.messages[0]["timestamp"][:10]
        return (
            f"📋 <b>Your Chat History</b>\n"
            f"Total messages : <b>{total}</b>\n"
            f"Your messages  : <b>{user_c}</b>\n"
            f"AI responses   : <b>{ai_c}</b>\n"
            f"First message  : <b>{first_ts}</b>"
        )


# ─────────────────────────────────────────────
#  TOOL EXECUTOR
# ─────────────────────────────────────────────
class ToolExecutor:
    _crypto_tools   = None
    _market_summary = None

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
    def run(cls, name: str, args: dict):
        try:
            if name == "get_crypto_history":
                return cls._ct().get_crypto_history(args.get("symbol"))
            elif name == "get_latest_news":
                return cls._ct().get_latest_news(args.get("symbol"))
            elif name == "fetch_and_analyze_asset_news":
                return cls._ct().fetch_and_analyze_asset_news(args.get("symbol"))
            elif name == "get_technical_analysis":
                return cls._ct().get_technical_analysis(args.get("symbol"))
            elif name == "get_current_points":
                return cls._ct().get_current_points(args.get("symbol"))
            elif name == "get_live_price":
                return cls._ct().get_live_price(args.get("symbol"))
            elif name == "get_top_gainers":
                return cls._ct().get_top_gainers(int(args.get("limit", 5)))
            elif name == "get_market_summary":
                return cls._ms().get_market_summary()
            elif name == "generate_chart":
                return generate_chart(
                    symbol   = args.get("symbol", "BTC"),
                    interval = args.get("interval", "4h"),
                    limit    = int(args.get("limit", 80))
                )
            elif name == "get_trade_setup":
                from trade_setup_agent import get_trade_setup
                return get_trade_setup(
                    symbol    = args.get("symbol", "BTC"),
                    timeframe = args.get("timeframe", "4h")
                )
            else:
                return {"error": f"Unknown function: {name}"}
        except Exception as e:
            return {"error": str(e)}


# ─────────────────────────────────────────────
#  IN-MEMORY USER STATE
# ─────────────────────────────────────────────
_user_histories: dict[int, UserHistory] = {}
_user_locks:     dict[int, asyncio.Lock] = {}


def get_history(user_id: int) -> UserHistory:
    if user_id not in _user_histories:
        _user_histories[user_id] = UserHistory(user_id)
    return _user_histories[user_id]


def get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


# ─────────────────────────────────────────────
#  TELEGRAM HELPERS
# ─────────────────────────────────────────────
async def safe_edit(msg, text: str, parse_mode=ParseMode.HTML):
    try:
        await msg.edit_text(text[:4000], parse_mode=parse_mode,
                            disable_web_page_preview=True)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 0.5)
        await safe_edit(msg, text, parse_mode)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            print(f"[EditError] {e}")


def build_tool_status(log: list[dict]) -> str:
    lines = ["⚙️ <b>Working on it…</b>\n"]
    for entry in log:
        icon   = TOOL_ICONS.get(entry["name"], "🔧")
        args_s = ", ".join(
            f"<i>{k}</i>=<code>{v}</code>" for k, v in entry["args"].items()
        ) or "—"
        status = entry.get("status", "⏳")
        lines.append(f"{status} {icon} <b>{entry['name']}</b>({args_s})")
    return "\n".join(lines)


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_markdown(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{1,3}(.*?)_{1,3}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─────────────────────────────────────────────
#  XML TOOL CALL PARSER
# ─────────────────────────────────────────────
def parse_xml_tool_calls(content: str) -> list:
    tool_calls = []
    pattern = r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>"
    for i, m in enumerate(re.finditer(pattern, content, re.DOTALL)):
        func_name  = m.group(1)
        params_raw = m.group(2)
        params     = {}
        for pm in re.finditer(r"<parameter=(\w+)>(.*?)</parameter>", params_raw, re.DOTALL):
            params[pm.group(1)] = pm.group(2).strip()
        tool_calls.append({
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": func_name, "arguments": json.dumps(params)}
        })
    return tool_calls


# ─────────────────────────────────────────────
#  CORE CHAT
# ─────────────────────────────────────────────
async def run_chat(user_id: int, user_input: str, status_msg) -> tuple[str, list[str]]:
    history     = get_history(user_id)
    chart_paths: list[str] = []
    trade_setup_response: str | None = None

    history.add("user", user_input)
    messages = history.get_api_messages()
    messages.append({"role": "user", "content": user_input})

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    loop = asyncio.get_event_loop()

    payload = {
        "model":       MODEL,
        "messages":    messages,
        "tools":       TOOLS_SCHEMA,
        "tool_choice": "auto",
        "temperature": 0.1,
        "max_tokens":  2000
    }

    response = await loop.run_in_executor(
        None,
        lambda: requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=120)
    )
    result = response.json()

    if "error" in result:
        raise RuntimeError(result["error"].get("message", "Unknown NVIDIA API error"))

    message = result["choices"][0]["message"]
    content = message.get("content") or ""

    if not message.get("tool_calls") and "<tool_call>" in content:
        xml_calls = parse_xml_tool_calls(content)
        if xml_calls:
            message["tool_calls"] = xml_calls

    if message.get("tool_calls"):
        messages.append(message)
        tool_log = []

        for tool_call in message["tool_calls"]:
            fn_name = tool_call["function"]["name"]
            fn_args = json.loads(tool_call["function"]["arguments"])

            tool_log.append({"name": fn_name, "args": fn_args, "status": "⏳"})
            await safe_edit(status_msg, build_tool_status(tool_log))

            fn_response = await loop.run_in_executor(
                None,
                lambda n=fn_name, a=fn_args: ToolExecutor.run(n, a)
            )

            if fn_name == "generate_chart" and isinstance(fn_response, dict):
                if fn_response.get("status") == "success" and fn_response.get("path"):
                    chart_paths.append(fn_response["path"])

            if fn_name == "get_trade_setup" and isinstance(fn_response, dict):
                if not fn_response.get("error"):
                    try:
                        from trade_setup_agent import format_trade_setup_message
                        trade_setup_response = format_trade_setup_message(fn_response)
                    except Exception:
                        pass

            tool_log[-1]["status"] = "✅"
            await safe_edit(status_msg, build_tool_status(tool_log))
            messages.append({
                "tool_call_id": tool_call["id"],
                "role":         "tool",
                "name":         fn_name,
                "content":      json.dumps(fn_response, ensure_ascii=False, default=str)
            })

        payload2 = {
            "model":       MODEL,
            "messages":    messages,
            "temperature": 0.1,
            "max_tokens":  1500
        }
        response2 = await loop.run_in_executor(
            None,
            lambda: requests.post(NVIDIA_URL, headers=headers, json=payload2, timeout=120)
        )
        result2 = response2.json()

        if "error" in result2:
            raise RuntimeError(result2["error"].get("message", "Unknown NVIDIA API error (2nd call)"))

        ai_response = strip_markdown(result2["choices"][0]["message"].get("content") or "")
    else:
        ai_response = strip_markdown(content)

    if not ai_response.strip() and trade_setup_response:
        ai_response = ""

    final_text = trade_setup_response if trade_setup_response and not ai_response else ai_response
    if not final_text:
        final_text = "I have retrieved the latest data. Ask a specific question for a detailed analysis."

    history.add("assistant", final_text)
    return final_text, chart_paths


# ─────────────────────────────────────────────
#  TELEGRAM COMMAND HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>TrendOracle Crypto AI Bot</b>\n\n"
        "Ask me anything about crypto — prices, analysis, trade setups, charts.\n\n"
        "Commands:\n"
        "  /clear   — Clear chat history\n"
        "  /history — Show history stats",
        parse_mode=ParseMode.HTML
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    h = get_history(user_id)
    h.clear()
    await update.message.reply_text("✅ History cleared.", parse_mode=ParseMode.HTML)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    h = get_history(user_id)
    await update.message.reply_text(h.summary(), parse_mode=ParseMode.HTML)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("ℹ️ This action is not supported in the bot.", parse_mode=ParseMode.HTML)


async def handle_non_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Please send text messages only.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    user_input = update.message.text.strip()

    if not user_input:
        return

    lock = get_lock(user_id)
    async with lock:
        status_msg = await update.message.reply_text(
            "⚙️ <b>Working on it…</b>", parse_mode=ParseMode.HTML
        )
        try:
            ai_response, chart_paths = await run_chat(user_id, user_input, status_msg)

            await status_msg.delete()

            if ai_response:
                await update.message.reply_text(
                    ai_response[:4096], parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )

            for path in chart_paths:
                if os.path.exists(path):
                    try:
                        with open(path, "rb") as img:
                            await update.message.reply_photo(photo=img)
                    except Exception as e:
                        print(f"[Bot] Chart send error: {e}")

        except Exception as e:
            print(f"[Bot] Error for user {user_id}: {e}")
            try:
                await status_msg.edit_text(
                    f"❌ <b>Error:</b> {escape_html(str(e)[:200])}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass


# ─────────────────────────────────────────────
#  BACKGROUND AGENTS
# ─────────────────────────────────────────────
def start_background_agents():
    import threading

    agents_dir = os.path.dirname(os.path.abspath(__file__))
    if agents_dir not in sys.path:
        sys.path.insert(0, agents_dir)

    try:
        import sub_agents_runner
        t1 = threading.Thread(target=sub_agents_runner.news_agent_loop,      daemon=True, name="NewsAgent")
        t2 = threading.Thread(target=sub_agents_runner.technical_agent_loop, daemon=True, name="TechAgent")
        t3 = threading.Thread(target=sub_agents_runner.whale_agent_loop,     daemon=True, name="WhaleAgent")
        t1.start(); t2.start(); t3.start()
        print("  ✅ Background sub-agents started (News | Technical | Whale)")
    except Exception as e:
        print(f"  ⚠️  Background agents failed to start: {e}")


def main():
    print("=" * 55)
    print("  🚀 CRYPTO AI TELEGRAM BOT — Starting up…")
    print("=" * 55)
    print(f"  Token : {TELEGRAM_BOT_TOKEN[:20]}…")
    print(f"  Model : {MODEL}")
    print("=" * 55)

    start_background_agents()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(~filters.TEXT, handle_non_text))

    print("  Bot is running. Press Ctrl+C to stop.\n")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

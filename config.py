"""
config.py — Central Configuration File
=======================================
මෙතන සිට API keys, tokens සහ settings edit කරන්න.
සෑම file එකක්ම මෙතනින් import කරනවා.
"""

import os

# ─────────────────────────────────────────────
#  NVIDIA AI API
# ─────────────────────────────────────────────
NVIDIA_API_KEY = ""
NVIDIA_URL     = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL          = "stepfun-ai/step-3.5-flash"

# ─────────────────────────────────────────────
#  TELEGRAM BOT
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = "5860415170"

# ─────────────────────────────────────────────
#  TELEGRAM NEWS BOT (crypto_news_reader)
# ─────────────────────────────────────────────
NEWS_TELEGRAM_BOT_TOKEN = ""
NEWS_TELEGRAM_CHAT_ID   = "5860415170"

# ─────────────────────────────────────────────
#  BITGET API
# ─────────────────────────────────────────────
BITGET_BASE     = "https://api.bitget.com/api/v2/spot/market"
BITGET_API_BASE = "https://api.bitget.com"

# ─────────────────────────────────────────────
#  SECURITY / JWT
# ─────────────────────────────────────────────
JWT_SECRET  = os.environ.get("JWT_SECRET", "CHANGE_ME_CRYPTO_JWT_SECRET_KEY_2025")
MASTER_KEY  = os.environ.get("MASTER_KEY", "")

# ─────────────────────────────────────────────
#  AI SETTINGS
# ─────────────────────────────────────────────
MAX_HISTORY    = 30
MAX_TOKENS     = 4000
TEMPERATURE    = 0.1

# ─────────────────────────────────────────────
#  TRACKED CRYPTO SYMBOLS
# ─────────────────────────────────────────────
TRACKED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "BNBUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT",
    "DOGEUSDT", "LTCUSDT", "LINKUSDT", "MATICUSDT",
    "ATOMUSDT", "UNIUSDT", "NEARUSDT", "FTMUSDT",
    "SHIBUSDT", "TRXUSDT", "XLMUSDT", "VETUSDT",
]

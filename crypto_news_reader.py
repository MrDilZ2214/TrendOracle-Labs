"""
agents/crypto_news_reader.py — Crypto News Reader & Analyzer (SQLite-backed)
=============================================================================
Fetches RSS feeds, AI-analyzes articles via NVIDIA API, stores results in SQLite.
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import requests
import feedparser
import time
import re
import uuid
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

import database

import config as _cfg
NVIDIA_API_KEY     = _cfg.NVIDIA_API_KEY
NVIDIA_URL         = _cfg.NVIDIA_URL
MODEL              = _cfg.MODEL
MAX_TOKENS         = _cfg.MAX_TOKENS
FETCH_PROXY        = "https://api.codetabs.com/v1/proxy?quest="
TELEGRAM_BOT_TOKEN = _cfg.NEWS_TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID   = _cfg.NEWS_TELEGRAM_CHAT_ID

FEEDS = {
    "CoinDesk":        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Investing.com":   "https://www.investing.com/rss/news_301.rss",
    "Cointelegraph":   "https://cointelegraph.com/rss",
    "Bitcoin.com":     "https://news.bitcoin.com/feed/",
    "Decrypt":         "https://decrypt.co/feed",
    "CryptoSlate":     "https://cryptoslate.com/feed/",
    "Bitcoin Magazine":"https://bitcoinmagazine.com/.rss/full/",
    "The Block":       "https://www.theblock.co/rss.xml",
    "BeInCrypto":      "https://beincrypto.com/feed/",
    "CryptoPotato":    "https://cryptopotato.com/feed/",
    "NewsBTC":         "https://www.newsbtc.com/feed/",
    "UToday":          "https://u.today/rss"
}

CRYPTO_KEYWORDS = {
    'bitcoin', 'ethereum', 'crypto', 'blockchain', 'btc', 'eth', 'sol', 'xrp',
    'bnb', 'defi', 'nft', 'web3', 'altcoin', 'stablecoin', 'coinbase', 'binance',
    'token', 'wallet', 'exchange', 'hodl', 'whale', 'satoshi', 'on-chain',
    'onchain', 'layer 2', 'layer-2', 'ada', 'dot', 'avax', 'link', 'matic',
    'polygon', 'solana', 'ripple', 'cardano', 'dogecoin', 'shib', 'doge',
    'glassnode', 'coingecko', 'coinmarketcap', 'cointelegraph', 'bitget',
    'kraken', 'bybit', 'okx', 'huobi', 'uniswap', 'aave', 'compound',
    'staking', 'mining', 'hashrate', 'mempool', 'halving', 'fork',
}


def is_crypto_title(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in CRYPTO_KEYWORDS)


# ── SQLite-backed processed links ─────────────────────────────────────────────

def load_processed_links():
    return database.processed_news_get_all()


def save_processed_link(link):
    database.processed_news_add(link)


# ── SQLite-backed news DB ─────────────────────────────────────────────────────

def load_news_db():
    default_db = {
        "total_points": {"up": 0, "down": 0, "total": 0},
        "coin_points":  {},
        "news":         []
    }
    db = database.kv_get("news")
    if not db:
        db = default_db

    now          = datetime.now(timezone.utc)
    expired_news = [n for n in db["news"] if datetime.fromisoformat(n["expires_at"]) < now]
    active_news  = [n for n in db["news"] if datetime.fromisoformat(n["expires_at"]) >= now]

    if expired_news:
        for n in expired_news:
            db["total_points"]["up"]    -= n["up_score"]
            db["total_points"]["down"]  -= n["down_score"]
            db["total_points"]["total"] -= 10
            for coin in n["affected_coins"]:
                if coin in db["coin_points"]:
                    db["coin_points"][coin]["up"]    -= n["up_score"]
                    db["coin_points"][coin]["down"]  -= n["down_score"]
                    db["coin_points"][coin]["total"] -= 10
                    if db["coin_points"][coin]["total"] <= 0:
                        del db["coin_points"][coin]
        db["news"] = active_news
        save_news_db(db)

    return db


def save_news_db(db):
    database.kv_set("news", db)


# ── RSS Feed helpers ──────────────────────────────────────────────────────────

def get_latest_news_feed(source_name, feed_url):
    try:
        print(f"Fetching news from {source_name}...")
        headers  = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(feed_url, headers=headers, timeout=15)
        feed     = feedparser.parse(response.content)

        if not feed.entries:
            print(f"Empty feed for {source_name}, trying proxy...")
            proxied_url = f"{FETCH_PROXY}{feed_url}"
            response    = requests.get(proxied_url, timeout=15)
            feed        = feedparser.parse(response.content)

        for entry in feed.entries:
            entry['source'] = source_name

        return sorted(feed.entries, key=lambda x: x.get('published_parsed', 0), reverse=True)
    except Exception as e:
        print(f"RSS Fetch Error ({source_name}): {e}")
        return []


def get_article_text(url):
    try:
        proxied_url = f"{FETCH_PROXY}{url}"
        response    = requests.get(proxied_url, timeout=15)
        soup        = BeautifulSoup(response.content, 'html.parser')
        paragraphs  = soup.find_all('p')
        text        = '\n'.join([p.get_text() for p in paragraphs if len(p.get_text()) > 40])
        return text[:8000]
    except Exception as e:
        print(f"Article Content Fetch Error: {e}")
        return ""


# ── AI Analysis ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT_AI_ANALYSIS = """You are an elite cryptocurrency market analyst. Analyze the provided news article.

Respond STRICTLY in this exact format, NO extra text:

CRYPTO_RELATED: YES or NO
SUMMARY: [2-3 sentence summary]
UP_SCORE: [integer 1-9]
UP_REASONS:
- [reason 1]
- [reason 2]
- [reason 3]
DOWN_REASONS:
- [reason 1]
- [reason 2]
- [reason 3]
IMPACT: LOW or MEDIUM or HIGH or VERY HIGH
AFFECTED_COINS: [comma-separated tickers, e.g. BTC,ETH,SOL — or ALL for general crypto news]
TIMEFRAME: SHORT-TERM or MID-TERM or LONG-TERM

Rules:
- UP_SCORE: how likely is market to go UP because of this news (1=very unlikely, 9=near certain)
- DOWN_SCORE is automatically 10 minus UP_SCORE, do NOT include it
- If news is NOT crypto related, only output CRYPTO_RELATED: NO and nothing else
- Always give exactly 3 UP_REASONS and 3 DOWN_REASONS
- AFFECTED_COINS: use standard tickers only (BTC not Bitcoin, ETH not Ethereum)
- If news affects all crypto generally, write ALL
"""


def call_nvidia_api(title, content):
    print(f"\n--- AI Analysis Start: {title} ---")
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type":  "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_AI_ANALYSIS},
            {"role": "user",   "content": f"Title: {title}\n\nContent: {content[:MAX_TOKENS]}"}
        ],
        "max_tokens": 500
    }
    try:
        print(f"Sending request to NVIDIA API...")
        response = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=60)
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content'] or ""
                print("AI successfully generated content.")
                return content
            else:
                print(f"Unexpected API response format: {result}")
                return ""
        else:
            print(f"API Error {response.status_code}: {response.text}")
            return ""
    except Exception as e:
        print(f"Exception during AI request: {type(e).__name__}: {str(e)}")
        return ""


def parse_ai_result(ai_result):
    parsed_data = {
        "crypto_related": False,
        "summary":        "",
        "up_score":       0,
        "up_reasons":     [],
        "down_reasons":   [],
        "impact":         "",
        "affected_coins": [],
        "timeframe":      ""
    }

    lines = ai_result.strip().split('\n')

    if lines and lines[0].startswith("CRYPTO_RELATED:"):
        if "YES" in lines[0]:
            parsed_data["crypto_related"] = True
        else:
            return parsed_data

    current_section = None
    for line in lines:
        line = line.strip()
        if line.startswith("SUMMARY:"):
            parsed_data["summary"]  = line.replace("SUMMARY:", "").strip()
            current_section = None
        elif line.startswith("UP_SCORE:"):
            try:
                parsed_data["up_score"] = int(line.replace("UP_SCORE:", "").strip())
            except ValueError:
                parsed_data["up_score"] = 0
            current_section = None
        elif line.startswith("UP_REASONS:"):
            current_section = "up_reasons"
        elif line.startswith("DOWN_REASONS:"):
            current_section = "down_reasons"
        elif line.startswith("IMPACT:"):
            parsed_data["impact"] = line.replace("IMPACT:", "").strip()
            current_section = None
        elif line.startswith("AFFECTED_COINS:"):
            coins_str = line.replace("AFFECTED_COINS:", "").strip()
            parsed_data["affected_coins"] = parse_affected_coins(coins_str)
            current_section = None
        elif line.startswith("TIMEFRAME:"):
            parsed_data["timeframe"] = line.replace("TIMEFRAME:", "").strip()
            current_section = None
        elif line.startswith("-") and current_section == "up_reasons":
            parsed_data["up_reasons"].append(line[1:].strip())
        elif line.startswith("-") and current_section == "down_reasons":
            parsed_data["down_reasons"].append(line[1:].strip())

    return parsed_data


def parse_affected_coins(coins_str):
    if coins_str.strip().upper() == "ALL":
        return ["BTC", "ETH", "BNB", "SOL", "XRP"]
    coins = [c.strip().upper() for c in coins_str.split(",") if c.strip()]
    return coins if coins else ["BTC"]


def update_points_on_save(news_db, news_item):
    up   = news_item["up_score"]
    down = news_item["down_score"]

    news_db["total_points"]["up"]    += up
    news_db["total_points"]["down"]  += down
    news_db["total_points"]["total"] += 10

    for coin in news_item["affected_coins"]:
        if coin not in news_db["coin_points"]:
            news_db["coin_points"][coin] = {"up": 0, "down": 0, "total": 0}
        news_db["coin_points"][coin]["up"]    += up
        news_db["coin_points"][coin]["down"]  += down
        news_db["coin_points"][coin]["total"] += 10


def generate_id():
    return str(uuid.uuid4().hex)


def analyze_and_save_news(title, content, link, source):
    raw_ai_result    = call_nvidia_api(title, content)
    parsed_ai_result = parse_ai_result(raw_ai_result)

    if not parsed_ai_result.get("crypto_related"):
        if is_crypto_title(title):
            parsed_ai_result["crypto_related"] = True
            if not parsed_ai_result.get("affected_coins"):
                parsed_ai_result["affected_coins"] = ["BTC"]
            if not parsed_ai_result.get("up_score"):
                parsed_ai_result["up_score"] = 5
            if not parsed_ai_result.get("summary"):
                parsed_ai_result["summary"] = title
            if not parsed_ai_result.get("impact"):
                parsed_ai_result["impact"] = "MEDIUM"
            if not parsed_ai_result.get("timeframe"):
                parsed_ai_result["timeframe"] = "SHORT-TERM"
        else:
            print(f"Skipping non-crypto related news: {title}")
            return None

    db = load_news_db()

    up_score   = parsed_ai_result["up_score"]
    down_score = 10 - up_score

    news_item = {
        "id":             generate_id(),
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "expires_at":     (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
        "source":         source,
        "title":          title,
        "link":           link,
        "summary":        parsed_ai_result["summary"],
        "up_score":       up_score,
        "down_score":     down_score,
        "up_reasons":     parsed_ai_result["up_reasons"],
        "down_reasons":   parsed_ai_result["down_reasons"],
        "impact":         parsed_ai_result["impact"],
        "affected_coins": parsed_ai_result["affected_coins"],
        "timeframe":      parsed_ai_result["timeframe"],
        "crypto_related": True
    }

    db["news"].append(news_item)
    update_points_on_save(db, news_item)
    save_news_db(db)

    return news_item


def send_telegram_news(news_item):
    up_score   = news_item["up_score"]
    down_score = news_item["down_score"]

    up_bar   = "#" * up_score   + "-" * (10 - up_score)
    down_bar = "#" * down_score + "-" * (10 - down_score)

    now_utc    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bull_lines = news_item["up_reasons"]   if news_item["up_reasons"]   else ["No strong bull signals"]
    bear_lines = news_item["down_reasons"] if news_item["down_reasons"] else ["No strong bear signals"]
    bull_block = "\n".join([f"  (+)  {r}" for r in bull_lines])
    bear_block = "\n".join([f"  (-)  {r}" for r in bear_lines])

    assets_str     = "  ".join([f"#{a.strip().upper()}" for a in news_item["affected_coins"]]) if news_item["affected_coins"] else "N/A"
    item_title     = news_item["title"]
    safe_title     = (item_title[:85] + "...") if len(item_title) > 85 else item_title
    item_link      = news_item["link"]
    item_source    = news_item["source"]
    item_summary   = news_item["summary"]
    item_impact    = news_item["impact"]
    item_timeframe = news_item["timeframe"]

    message = (
        f"____________________________\n"
        f"<b>  CRYPTO NEWS ALERT</b>\n"
        f"<b><a href='{item_link}'>{item_source}</a></b>   |   <i>{now_utc}</i>\n"
        f"----------------------------\n\n"
        f"<b><a href='{item_link}'>{safe_title}</a></b>\n\n"
        f"<b>SUMMARY</b>\n"
        f"<i>{item_summary}</i>\n\n"
        f"============================\n"
        f"<b>  SENTIMENT ANALYSIS</b>\n"
        f"----------------------------\n\n"
        f"<b>UP   </b><code>[{up_bar}]</code>  <b>{up_score}/10</b>\n"
        f"<b>DOWN </b><code>[{down_bar}]</code>  <b>{down_score}/10</b>\n\n"
        f"============================\n"
        f"<b>  BULL CASE</b>\n"
        f"----------------------------\n"
        f"{bull_block}\n\n"
        f"<b>  BEAR CASE</b>\n"
        f"----------------------------\n"
        f"{bear_block}\n\n"
        f"============================\n"
        f"<b>  DETAILS</b>\n"
        f"----------------------------\n"
        f"<b>Impact    </b>  [{item_impact}]  {item_impact}\n"
        f"<b>Timeframe </b>  [{item_timeframe}]  {item_timeframe}\n"
        f"<b>Assets    </b>  {assets_str}\n"
        f"____________________________"
    )

    if len(message) > 4000:
        message = message[:3980] + "\n...\n____________________________"

    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     message,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True
        })
        if resp.status_code == 200:
            print(f"Telegram message from {item_source} sent successfully.")
            return True
        else:
            print(f"Telegram Error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"Telegram Exception: {e}")
        return False

"""
core/database.py — SQLite database manager
==========================================
Single SQLite database at data/trendoracle.db.
Replaces all JSON file storage (users.json, jwt_blacklist.json,
user_bitget_keys.json, technical.json, whale.json, news.json,
market_summary.json, processed_news.json, user_histories/,
user_settings/, pending_trades/, demo_accounts/).
"""
import sqlite3
import json
import os
import threading
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH  = os.path.join(DATA_DIR, "trendoracle.db")

os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    with _lock:
        conn = _get_conn()
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email        TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL UNIQUE,
                username     TEXT NOT NULL,
                pw_hash      TEXT DEFAULT '',
                plan         TEXT DEFAULT 'free',
                created_at   TEXT,
                avatar_color TEXT DEFAULT '',
                avatar_emoji TEXT DEFAULT ''
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS bitget_keys (
                user_id        TEXT PRIMARY KEY,
                api_key_enc    TEXT NOT NULL,
                secret_enc     TEXT NOT NULL,
                passphrase_enc TEXT NOT NULL,
                saved_at       TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS jwt_blacklist (
                jti             TEXT PRIMARY KEY,
                blacklisted_at  INTEGER NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS processed_news (
                link         TEXT PRIMARY KEY,
                processed_at TEXT DEFAULT (datetime('now'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_histories (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_hist_user ON user_histories(user_id)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id       TEXT PRIMARY KEY,
                settings_json TEXT NOT NULL DEFAULT '{}'
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_trades (
                trade_id   TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                data_json  TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON pending_trades(user_id)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS demo_accounts (
                user_id    TEXT PRIMARY KEY,
                data_json  TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS confirmed_trade_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                trade_id    TEXT NOT NULL UNIQUE,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                size        REAL NOT NULL,
                price       REAL,
                sl          REAL,
                tp          REAL,
                order_id    TEXT,
                status      TEXT DEFAULT 'confirmed',
                reason      TEXT,
                executed_at TEXT,
                data_json   TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_ctlog_user ON confirmed_trade_log(user_id)")

        conn.commit()
        conn.close()


_init_db()


# ── Key-Value Store ────────────────────────────────────────────────────────────

def kv_get(key: str) -> dict:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else {}
    finally:
        conn.close()


def kv_set(key: str, value: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?,?,?)",
                (key, json.dumps(value, default=str), datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()


# ── Users ──────────────────────────────────────────────────────────────────────

def users_get_all() -> dict:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return {
            row["email"]: {
                "id":           row["user_id"],
                "username":     row["username"],
                "pw_hash":      row["pw_hash"],
                "plan":         row["plan"],
                "created_at":   row["created_at"],
                "avatar_color": row["avatar_color"],
                "avatar_emoji": row["avatar_emoji"],
            }
            for row in rows
        }
    finally:
        conn.close()


def users_save_all(users: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM users")
            for email, u in users.items():
                conn.execute(
                    """INSERT OR REPLACE INTO users
                       (email, user_id, username, pw_hash, plan, created_at, avatar_color, avatar_emoji)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (email, u.get("id", ""), u.get("username", ""), u.get("pw_hash", ""),
                     u.get("plan", "free"), u.get("created_at", ""),
                     u.get("avatar_color", ""), u.get("avatar_emoji", ""))
                )
            conn.commit()
        finally:
            conn.close()


def user_upsert(email: str, u: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO users
                   (email, user_id, username, pw_hash, plan, created_at, avatar_color, avatar_emoji)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (email, u.get("id", ""), u.get("username", ""), u.get("pw_hash", ""),
                 u.get("plan", "free"), u.get("created_at", ""),
                 u.get("avatar_color", ""), u.get("avatar_emoji", ""))
            )
            conn.commit()
        finally:
            conn.close()


# ── Bitget Keys ────────────────────────────────────────────────────────────────

def keys_get_all() -> dict:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM bitget_keys").fetchall()
        return {
            row["user_id"]: {
                "api_key_enc":    row["api_key_enc"],
                "secret_enc":     row["secret_enc"],
                "passphrase_enc": row["passphrase_enc"],
                "saved_at":       row["saved_at"],
            }
            for row in rows
        }
    finally:
        conn.close()


def key_upsert(user_id: str, k: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO bitget_keys
                   (user_id, api_key_enc, secret_enc, passphrase_enc, saved_at)
                   VALUES (?,?,?,?,?)""",
                (user_id, k.get("api_key_enc", ""), k.get("secret_enc", ""),
                 k.get("passphrase_enc", ""), k.get("saved_at", ""))
            )
            conn.commit()
        finally:
            conn.close()


def key_delete(user_id: str):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM bitget_keys WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()


def key_exists(user_id: str) -> bool:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM bitget_keys WHERE user_id = ?", (user_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def key_get(user_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM bitget_keys WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


# ── JWT Blacklist ──────────────────────────────────────────────────────────────

def blacklist_add(jti: str, ts: int):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO jwt_blacklist (jti, blacklisted_at) VALUES (?,?)",
                (jti, ts)
            )
            conn.commit()
        finally:
            conn.close()


def blacklist_exists(jti: str) -> bool:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM jwt_blacklist WHERE jti = ?", (jti,)
        ).fetchone() is not None
    finally:
        conn.close()


def blacklist_purge(cutoff: int):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM jwt_blacklist WHERE blacklisted_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()


# ── Processed News ─────────────────────────────────────────────────────────────

def processed_news_get_all() -> set:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT link FROM processed_news").fetchall()
        return {row["link"] for row in rows}
    finally:
        conn.close()


def processed_news_add(link: str):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO processed_news (link, processed_at) VALUES (?,?)",
                (link, datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()


# ── User Histories ─────────────────────────────────────────────────────────────

def history_get(user_id: str) -> list:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT timestamp, role, content FROM user_histories WHERE user_id = ? ORDER BY id",
            (user_id,)
        ).fetchall()
        return [{"timestamp": r["timestamp"], "role": r["role"], "content": r["content"]} for r in rows]
    finally:
        conn.close()


def history_save(user_id: str, messages: list):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM user_histories WHERE user_id = ?", (user_id,))
            for m in messages:
                conn.execute(
                    "INSERT INTO user_histories (user_id, timestamp, role, content) VALUES (?,?,?,?)",
                    (user_id, m.get("timestamp", ""), m.get("role", ""), m.get("content", ""))
                )
            conn.commit()
        finally:
            conn.close()


# ── User Settings ──────────────────────────────────────────────────────────────

def settings_get(user_id: str) -> dict:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        return json.loads(row["settings_json"]) if row else {}
    finally:
        conn.close()


def settings_save(user_id: str, settings: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO user_settings (user_id, settings_json) VALUES (?,?)",
                (user_id, json.dumps(settings))
            )
            conn.commit()
        finally:
            conn.close()


# ── Pending Trades ─────────────────────────────────────────────────────────────

def trades_get(user_id: str) -> dict:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT trade_id, data_json FROM pending_trades WHERE user_id = ?",
            (user_id,)
        ).fetchall()
        return {row["trade_id"]: json.loads(row["data_json"]) for row in rows}
    finally:
        conn.close()


def trades_save(user_id: str, trades: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM pending_trades WHERE user_id = ?", (user_id,))
            for tid, t in trades.items():
                conn.execute(
                    "INSERT INTO pending_trades (trade_id, user_id, data_json, created_at) VALUES (?,?,?,?)",
                    (tid, user_id, json.dumps(t, default=str), t.get("created_at", 0))
                )
            conn.commit()
        finally:
            conn.close()


def trades_get_all_user_ids() -> list:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT user_id FROM pending_trades").fetchall()
        return [row["user_id"] for row in rows]
    finally:
        conn.close()


# ── Demo Accounts ──────────────────────────────────────────────────────────────

def demo_get(user_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT data_json FROM demo_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return json.loads(row["data_json"]) if row else None
    finally:
        conn.close()


def demo_save(user_id: str, data: dict):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO demo_accounts (user_id, data_json, updated_at)
                   VALUES (?,?,?)""",
                (user_id, json.dumps(data, default=str), datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()


# ── Confirmed Trade Log ────────────────────────────────────────────────────────

def trade_log_save(user_id: str, trade: dict, order_id: str = ""):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO confirmed_trade_log
                   (user_id, trade_id, symbol, side, size, price, sl, tp,
                    order_id, status, reason, executed_at, data_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    trade.get("trade_id", ""),
                    trade.get("symbol", ""),
                    trade.get("side", ""),
                    float(trade.get("size", 0)),
                    float(trade.get("price") or 0) or None,
                    float(trade.get("sl") or 0) or None,
                    float(trade.get("tp") or 0) or None,
                    order_id,
                    trade.get("status", "confirmed"),
                    trade.get("reason", ""),
                    trade.get("executed_at", datetime.now().isoformat()),
                    json.dumps(trade, default=str),
                )
            )
            conn.commit()
        finally:
            conn.close()


def trade_log_get(user_id: str, limit: int = 50) -> list:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT data_json, order_id, executed_at FROM confirmed_trade_log
               WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
            (user_id, limit)
        ).fetchall()
        results = []
        for row in rows:
            try:
                t = json.loads(row["data_json"])
                if row["order_id"]:
                    t["order_id"] = row["order_id"]
                if row["executed_at"]:
                    t["executed_at"] = row["executed_at"]
                results.append(t)
            except Exception:
                pass
        return results
    finally:
        conn.close()

"""
core/bitget_client.py — Bitget Private API Client (SQLite-backed key storage)
==============================================================================
  • Encrypted key storage per user (Fernet / MASTER_KEY) in SQLite
  • HMAC-SHA256 signed requests for private endpoints
  • Spot trading: place_order, cancel_order
  • Account queries: balance, open_orders, order_history
"""
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime

import requests
from cryptography.fernet import Fernet

import database

import sys as _sys
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in _sys.path:
    _sys.path.insert(0, _ROOT_DIR)
import config as _cfg

_raw_master = _cfg.MASTER_KEY
if _raw_master:
    try:
        FERNET = Fernet(_raw_master.encode())
    except Exception:
        FERNET = None
else:
    _dev_secret = b"CRYPTEX_DEV_MASTER_KEY_CHANGE_IN_PROD"
    _key = base64.urlsafe_b64encode(hashlib.sha256(_dev_secret).digest())
    FERNET = Fernet(_key)

BITGET_API_BASE = _cfg.BITGET_API_BASE


def _encrypt(s: str) -> str:
    if FERNET is None:
        return s
    return FERNET.encrypt(s.encode()).decode()


def _decrypt(s: str) -> str:
    if FERNET is None:
        return s
    return FERNET.decrypt(s.encode()).decode()


# ── Key storage ───────────────────────────────────────────────────────────────

def save_keys(user_id: str, api_key: str, secret: str, passphrase: str):
    database.key_upsert(user_id, {
        "api_key_enc":    _encrypt(api_key),
        "secret_enc":     _encrypt(secret),
        "passphrase_enc": _encrypt(passphrase),
        "saved_at":       datetime.utcnow().isoformat(),
    })


def load_keys(user_id: str) -> dict | None:
    rec = database.key_get(str(user_id))
    if not rec:
        return None
    try:
        return {
            "api_key":    _decrypt(rec["api_key_enc"]),
            "secret":     _decrypt(rec["secret_enc"]),
            "passphrase": _decrypt(rec["passphrase_enc"]),
        }
    except Exception:
        return None


def keys_exist(user_id: str) -> bool:
    return database.key_exists(str(user_id))


def delete_keys(user_id: str):
    database.key_delete(str(user_id))


# ── HMAC-SHA256 signing ───────────────────────────────────────────────────────

def _ts_ms() -> str:
    return str(int(time.time() * 1000))


def _sign(secret: str, ts: str, method: str, path: str, body: str = "") -> str:
    msg = ts + method.upper() + path + body
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()


def _private_request(user_id: str, method: str, path: str, params: dict = None, body: dict = None) -> dict:
    creds = load_keys(user_id)
    if not creds:
        raise ValueError("No Bitget API keys found for this user")

    ts     = _ts_ms()
    body_s = json.dumps(body, separators=(",", ":")) if body else ""
    sign   = _sign(creds["secret"], ts, method, path, body_s)

    headers = {
        "ACCESS-KEY":        creds["api_key"],
        "ACCESS-SIGN":       sign,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": creds["passphrase"],
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }

    url = BITGET_API_BASE + path
    try:
        if method.upper() == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=15)
        else:
            r = requests.post(url, headers=headers, data=body_s, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {"code": "-1", "msg": str(e), "data": None}


# ── Trading ───────────────────────────────────────────────────────────────────

def place_order(user_id: str, symbol: str, side: str, size: float,
                price: float = None, sl: float = None, tp: float = None) -> dict:
    order_type = "limit" if price else "market"
    body = {
        "symbol":    symbol.upper(),
        "side":      side.lower(),
        "orderType": order_type,
        "size":      str(size),
        "force":     "gtc" if order_type == "limit" else "fok",
    }
    if price:
        body["price"] = str(price)
    return _private_request(user_id, "POST", "/api/v2/spot/trade/place-order", body=body)


def cancel_order(user_id: str, order_id: str, symbol: str) -> dict:
    body = {"orderId": order_id, "symbol": symbol.upper()}
    return _private_request(user_id, "POST", "/api/v2/spot/trade/cancel-order", body=body)


def get_account_balance(user_id: str) -> dict:
    resp = _private_request(user_id, "GET", "/api/v2/spot/account/assets")
    if resp.get("code") == "00000":
        assets = resp.get("data", [])
        nonzero = [a for a in assets if float(a.get("available", 0)) > 0 or float(a.get("frozen", 0)) > 0]
        return {"ok": True, "assets": nonzero}
    return {"ok": False, "error": resp.get("msg", "Unknown error")}


def get_open_orders(user_id: str, symbol: str = None) -> dict:
    params = {}
    if symbol:
        params["symbol"] = symbol.upper()
    resp = _private_request(user_id, "GET", "/api/v2/spot/trade/unfilled-orders", params=params)
    if resp.get("code") == "00000":
        return {"ok": True, "orders": resp.get("data", [])}
    return {"ok": False, "error": resp.get("msg", "Unknown error")}


def get_order_history(user_id: str, symbol: str = None, limit: int = 20) -> dict:
    params = {"limit": str(limit)}
    if symbol:
        params["symbol"] = symbol.upper()
    resp = _private_request(user_id, "GET", "/api/v2/spot/trade/history-orders", params=params)
    if resp.get("code") == "00000":
        return {"ok": True, "orders": resp.get("data", [])}
    return {"ok": False, "error": resp.get("msg", "Unknown error")}


def verify_keys(user_id: str) -> dict:
    resp = _private_request(user_id, "GET", "/api/v2/spot/account/info")
    if resp.get("code") == "00000":
        return {"ok": True, "account": resp.get("data", {})}
    return {"ok": False, "error": resp.get("msg", "Invalid API keys or permissions")}

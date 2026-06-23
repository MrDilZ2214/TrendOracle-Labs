"""
core/ws_dispatcher.py — WebSocket Action Dispatcher
=====================================================
Unified action handler for /ws endpoint.

Message envelope (client → server):
  { "action": "auth.login", "id": "req_123", "data": {...} }

Response envelope (server → client):
  { "type": "response", "id": "req_123", "ok": true/false, "data": {...} }
  or server-pushed events (no "id"):
  { "type": "trade_confirm", "data": {...} }

Auth middleware: actions marked with REQUIRES_AUTH need a valid JWT in the WS session.
After auth.attach succeeds, the socket carries user_id for the session lifetime.
"""

import asyncio
import json
import os
import time
from datetime import datetime

import jwt as pyjwt

import auth_utils
import bitget_client
import trade_manager
import demo_trading
import database
from price_cache import _price_cache, _price_cache_lock, _market_cache, get_cached_price, get_fresh_price, add_tracked_symbol


# ── User settings helpers (now SQLite-backed) ─────────────────────────────────

def load_settings(user_id):
    return database.settings_get(str(user_id))


def save_settings(user_id, settings):
    database.settings_save(str(user_id), settings)


# ── Session state per WS connection ───────────────────────────────────────────
class WsSession:
    def __init__(self, websocket):
        self.ws            = websocket
        self.user_id       = None
        self.authenticated = False
        self.subscribed    = False

    async def send(self, payload: dict):
        try:
            await self.ws.send_json(payload)
        except Exception:
            pass

    async def respond(self, req_id, ok, data=None, error=None):
        msg = {"type": "response", "id": req_id, "ok": ok}
        if data  is not None: msg["data"]  = data
        if error is not None: msg["error"] = error
        await self.send(msg)

    async def push(self, event_type: str, data: dict):
        await self.send({"type": event_type, **data})


# ── Actions requiring auth ─────────────────────────────────────────────────────
REQUIRES_AUTH = {
    "auth.logout", "auth.refresh",
    "user.bitget.save", "user.bitget.status", "user.bitget.delete",
    "user.settings.get", "user.settings.set",
    "chat.send", "chat.reset",
    "trade.confirm", "trade.reject", "trade.list_pending", "trade.history",
    "account.balance", "account.orders", "account.cancel",
    "dash.subscribe", "dash.unsubscribe",
    "demo.trade", "demo.close", "demo.balance", "demo.reset",
    "chat.history", "chat.list",
    "prices.get",
}


async def dispatch(session: WsSession, raw: str, get_history_fn, lock_fn, run_chat_fn, broadcast_dash_subscribe_fn):
    """Main dispatcher: parse message, check auth, route to handler."""
    try:
        msg = json.loads(raw)
    except Exception:
        await session.send({"type": "error", "error": "Invalid JSON"})
        return

    action = msg.get("action", msg.get("type", ""))
    req_id = msg.get("id")
    data   = msg.get("data", {})

    # Legacy ping
    if action == "ping" or raw == "ping":
        await session.send({"type": "pong"})
        return

    # Auth guard
    if action in REQUIRES_AUTH and not session.authenticated:
        await session.respond(req_id, False, error="Authentication required")
        return

    # ── Standard Login (email + password) ─────────────────────────────────────
    if action == "auth.login":
        email    = data.get("email", "").strip()
        password = data.get("password", "")
        if not email or not password:
            await session.respond(req_id, False, error="Email and password are required")
            return
        result = auth_utils.login_user(email, password)
        if not result["ok"]:
            await session.respond(req_id, False, error=result["error"])
            return
        tokens = auth_utils.issue_tokens(result["user_id"])
        session.user_id       = result["user_id"]
        session.authenticated = True
        user = auth_utils.get_user_by_email(email)
        await session.respond(req_id, True, data={
            **tokens,
            "username":   result["username"],
            "user_id":    result["user_id"],
            "email":      email,
            "plan":       result.get("plan", "free"),
            "created_at": user.get("created_at", "") if user else "",
        })
        return

    # ── OTP: Send (register + forgot password) ────────────────────────────────
    if action == "auth.send_otp":
        email    = data.get("email", "").strip().lower()
        otp_type = data.get("otp_type", "register")
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not email or "@" not in email:
            await session.respond(req_id, False, error="Valid email required")
            return

        if otp_type == "register":
            if not username:
                await session.respond(req_id, False, error="Username required for registration")
                return
            if not password or len(password) < 6:
                await session.respond(req_id, False, error="Password must be at least 6 characters")
                return
            if auth_utils.email_exists(email):
                await session.respond(req_id, False, error="Email already registered. Please login.")
                return
            extra = {"username": username, "password": password}
        elif otp_type == "reset":
            if not auth_utils.email_exists(email):
                await session.respond(req_id, False, error="Email not found. Please register first.")
                return
            extra = {}
        else:
            await session.respond(req_id, False, error="Invalid OTP type")
            return

        code   = auth_utils.store_otp(email, otp_type, extra=extra)
        result = auth_utils.send_otp_email(email, code, otp_type)

        if not result["ok"]:
            await session.respond(req_id, False, error=f"Failed to send OTP: {result.get('error','')}")
            return

        dev_note = " (check console in dev mode)" if result.get("dev_mode") else ""
        await session.respond(req_id, True, data={
            "message": f"Verification code sent to {email}{dev_note}",
            "email":   email,
        })
        return

    # ── OTP: Verify register ───────────────────────────────────────────────────
    if action == "auth.verify_otp":
        email    = data.get("email", "").strip().lower()
        code     = data.get("code", "").strip()
        otp_type = data.get("otp_type", "register")

        if not email or not code:
            await session.respond(req_id, False, error="email and code required")
            return

        result = auth_utils.verify_otp(email, code, otp_type)
        if not result["ok"]:
            await session.respond(req_id, False, error=result["error"])
            return

        extra = result.get("extra", {})

        if otp_type == "register":
            username = extra.get("username", "")
            password = extra.get("password", "")
            reg = auth_utils.register_user(username, email, password)
            if not reg["ok"]:
                await session.respond(req_id, False, error=reg["error"])
                return
            user_id  = reg["user_id"]
            username = reg["username"]
            tokens   = auth_utils.issue_tokens(user_id)
            session.user_id       = user_id
            session.authenticated = True
            user = auth_utils.get_user_by_email(email)
            await session.respond(req_id, True, data={
                **tokens,
                "username":   username,
                "user_id":    user_id,
                "email":      email,
                "plan":       "free",
                "created_at": user.get("created_at", "") if user else "",
                "new_user":   True,
            })
            return
        else:
            await session.respond(req_id, False, error="Invalid OTP type for verify_otp")
            return

    # ── Forgot password: verify OTP + set new password ────────────────────────
    if action == "auth.reset_password":
        email        = data.get("email", "").strip().lower()
        code         = data.get("code", "").strip()
        new_password = data.get("new_password", "").strip()

        if not email or not code or not new_password:
            await session.respond(req_id, False, error="email, code, and new_password are required")
            return
        if len(new_password) < 6:
            await session.respond(req_id, False, error="Password must be at least 6 characters")
            return

        result = auth_utils.verify_otp(email, code, "reset")
        if not result["ok"]:
            await session.respond(req_id, False, error=result["error"])
            return

        upd = auth_utils.update_password(email, new_password)
        if not upd["ok"]:
            await session.respond(req_id, False, error=upd["error"])
            return

        await session.respond(req_id, True, data={"message": "Password updated successfully"})
        return

    # ── Attach session (token auth) ────────────────────────────────────────────
    if action == "auth.attach":
        token = data.get("token", "")
        try:
            payload = auth_utils.decode_jwt(token)
            if payload.get("type") != "access":
                raise pyjwt.InvalidTokenError("Not an access token")
            session.user_id       = payload["sub"]
            session.authenticated = True
            user = auth_utils.get_user_by_id(session.user_id)
            await session.respond(req_id, True, data={
                "user_id":    session.user_id,
                "username":   user["username"] if user else "User",
                "plan":       user.get("plan", "free") if user else "free",
                "email":      user.get("email", "") if user else "",
                "created_at": user.get("created_at", "") if user else "",
            })
        except pyjwt.ExpiredSignatureError:
            await session.respond(req_id, False, error="Token expired")
        except Exception as e:
            await session.respond(req_id, False, error=f"Invalid token: {e}")
        return

    if action == "auth.refresh":
        token = data.get("refresh_token", "")
        try:
            payload = auth_utils.decode_jwt(token)
            if payload.get("type") != "refresh":
                raise pyjwt.InvalidTokenError("Not a refresh token")
            tokens = auth_utils.issue_tokens(payload["sub"])
            await session.respond(req_id, True, data=tokens)
        except pyjwt.ExpiredSignatureError:
            await session.respond(req_id, False, error="Refresh token expired — please log in again")
        except Exception as e:
            await session.respond(req_id, False, error=str(e))
        return

    if action == "auth.logout":
        token = data.get("token", "")
        if token:
            try:
                payload = auth_utils.decode_jwt(token)
                auth_utils.blacklist_token(payload.get("jti", ""))
            except Exception:
                pass
        session.authenticated = False
        session.user_id       = None
        await session.respond(req_id, True, data={"message": "Logged out"})
        return

    # ── Bitget key management ─────────────────────────────────────────────────
    if action == "user.bitget.save":
        api_key    = data.get("api_key", "")
        secret     = data.get("secret", "")
        passphrase = data.get("passphrase", "")
        if not (api_key and secret and passphrase):
            await session.respond(req_id, False, error="api_key, secret, and passphrase are required")
            return
        bitget_client.save_keys(session.user_id, api_key, secret, passphrase)
        verify = bitget_client.verify_keys(session.user_id)
        if not verify["ok"]:
            bitget_client.delete_keys(session.user_id)
            await session.respond(req_id, False, error=f"Key verification failed: {verify['error']}")
            return
        await session.respond(req_id, True, data={"message": "Bitget API keys saved and verified"})
        return

    if action == "user.bitget.status":
        exists = bitget_client.keys_exist(session.user_id)
        await session.respond(req_id, True, data={"connected": exists})
        return

    if action == "user.bitget.delete":
        bitget_client.delete_keys(session.user_id)
        await session.respond(req_id, True, data={"message": "Bitget API keys removed"})
        return

    # ── User settings ─────────────────────────────────────────────────────────
    if action == "user.settings.get":
        settings = load_settings(session.user_id)
        await session.respond(req_id, True, data=settings)
        return

    if action == "user.settings.set":
        settings = load_settings(session.user_id)
        settings.update(data)
        save_settings(session.user_id, settings)
        await session.respond(req_id, True, data=settings)
        return

    # ── Chat ─────────────────────────────────────────────────────────────────
    if action == "chat.reset" or (action == "reset" and session.authenticated):
        get_history_fn(session.user_id).clear()
        await session.respond(req_id, True, data={"message": "New session started"})
        await session.send({"type": "system", "content": "New session started."})
        await session.send({"type": "status", "content": ""})
        return

    if action == "chat.history":
        history = get_history_fn(session.user_id)
        msgs = [m for m in history.messages if m["role"] in ("user", "assistant")][-40:]
        await session.respond(req_id, True, data={"messages": msgs})
        return

    if action == "chat.send":
        user_input = data.get("content", "").strip() if isinstance(data, dict) else data.strip()
        if not user_input:
            await session.respond(req_id, False, error="Empty message")
            return
        asyncio.ensure_future(
            _run_chat_task(session, user_input, get_history_fn, lock_fn, run_chat_fn)
        )
        return

    if action == "message" and session.authenticated:
        user_input = msg.get("content", "").strip()
        if user_input:
            asyncio.ensure_future(
                _run_chat_task(session, user_input, get_history_fn, lock_fn, run_chat_fn)
            )
        return

    # ── Trade management ──────────────────────────────────────────────────────
    if action == "trade.list_pending":
        trades = trade_manager.get_pending(session.user_id)
        await session.respond(req_id, True, data={"trades": trades})
        return

    if action == "trade.confirm":
        trade_id = data.get("trade_id", "")
        if not trade_id:
            await session.respond(req_id, False, error="trade_id required")
            return
        if not bitget_client.keys_exist(session.user_id):
            await session.respond(req_id, False, error="No Bitget API keys. Connect your account first in Settings.")
            return
        result = trade_manager.confirm_trade(session.user_id, trade_id)
        await session.respond(req_id, result["ok"], data=result)
        if result["ok"]:
            await session.push("order_update", {"data": result})
        return

    if action == "trade.reject":
        trade_id = data.get("trade_id", "")
        result = trade_manager.reject_trade(session.user_id, trade_id)
        await session.respond(req_id, result["ok"], data=result)
        return

    # ── Demo Trading ──────────────────────────────────────────────────────────
    if action == "demo.balance":
        with _price_cache_lock:
            prices = {
                sym.replace("USDT", ""): {"price": v["price"]}
                for sym, v in _price_cache.items()
            }
        acc_raw = demo_trading.get_account(session.user_id)
        for pos in acc_raw.get("positions", []):
            sym_clean = pos["symbol"].replace("/USDT", "").replace("USDT", "")
            if sym_clean not in prices:
                pd_ = get_cached_price(sym_clean + "USDT")
                if pd_ and pd_.get("price"):
                    prices[sym_clean] = {"price": pd_["price"]}
        acc = demo_trading.get_positions_with_pnl(session.user_id, prices)
        await session.respond(req_id, True, data=acc)
        return

    if action == "demo.trade":
        symbol    = data.get("symbol", "BTC").upper().replace("USDT", "") + "USDT"
        side      = data.get("side", "buy").lower()
        size_usdt = float(data.get("size_usdt", 100))
        sl        = data.get("sl")
        tp        = data.get("tp")
        reason    = data.get("reason", "")

        add_tracked_symbol(symbol)
        # NOTE: get_cached_price() can return a value up to 5s stale because
        # it's served from the background refresh loop. When several trades
        # are opened back-to-back (e.g. multiple AI/manual setups within the
        # same 5s window) they would all read the exact same cached tick and
        # therefore show an identical ENTRY price for every position. Fetch
        # the live ticker directly here so every trade gets its own real,
        # independent entry price.
        price_data = get_fresh_price(symbol)
        if not price_data or not price_data.get("price"):
            await session.respond(req_id, False, error=f"Could not fetch live price for {symbol}")
            return

        price  = price_data["price"]
        result = demo_trading.place_demo_trade(
            session.user_id, symbol, side, size_usdt, price,
            sl=float(sl) if sl else None,
            tp=float(tp) if tp else None,
            reason=reason,
        )
        await session.respond(req_id, result["ok"],
                              data=result if result["ok"] else None,
                              error=result.get("error") if not result["ok"] else None)
        if result["ok"]:
            await session.push("demo_update", {"data": result})
        return

    if action == "demo.close":
        trade_id = data.get("trade_id", "")
        if not trade_id:
            await session.respond(req_id, False, error="trade_id required")
            return
        acc = demo_trading.get_account(session.user_id)
        pos = next((p for p in acc["positions"] if p["trade_id"] == trade_id), None)
        if not pos:
            await session.respond(req_id, False, error="Position not found")
            return
        sym          = pos["symbol"].replace("/USDT", "") + "USDT"
        client_price = float(data.get("live_price", 0)) if data.get("live_price") else 0
        if client_price > 0:
            price = client_price
        else:
            pd_   = get_cached_price(sym)
            price = pd_.get("price", pos["entry_price"]) if pd_ else pos["entry_price"]
        result = demo_trading.close_demo_position(session.user_id, trade_id, price)
        await session.respond(req_id, result["ok"], data=result,
                              error=result.get("error") if not result["ok"] else None)
        return

    if action == "demo.reset":
        acc = demo_trading.reset_account(session.user_id)
        await session.respond(req_id, True, data={
            "message": "Demo account reset to $10,000",
            "balance": acc["balance_usdt"],
        })
        return

    # ── Account (live Bitget) ─────────────────────────────────────────────────
    if action == "account.balance":
        if not bitget_client.keys_exist(session.user_id):
            await session.respond(req_id, False, error="Bitget not connected")
            return
        result = bitget_client.get_account_balance(session.user_id)
        await session.respond(req_id, result["ok"], data=result)
        return

    if action == "account.orders":
        symbol      = data.get("symbol")
        orders_open = bitget_client.get_open_orders(session.user_id, symbol)
        orders_hist = bitget_client.get_order_history(session.user_id, symbol)
        await session.respond(req_id, True, data={
            "open":    orders_open.get("orders", []),
            "history": orders_hist.get("orders", []),
        })
        return

    if action == "account.cancel":
        order_id = data.get("order_id", "")
        symbol   = data.get("symbol", "")
        result   = bitget_client.cancel_order(session.user_id, order_id, symbol)
        ok = result.get("code") == "00000"
        await session.respond(req_id, ok, data=result if ok else None,
                              error=result.get("msg") if not ok else None)
        return

    # ── Dashboard subscription ────────────────────────────────────────────────
    if action == "dash.subscribe":
        session.subscribed = True
        await broadcast_dash_subscribe_fn(session.ws)
        await session.respond(req_id, True, data={"message": "Subscribed to dashboard"})
        return

    if action == "dash.unsubscribe":
        session.subscribed = False
        await session.respond(req_id, True, data={"message": "Unsubscribed"})
        return

    # ── Prices (live snapshot) ────────────────────────────────────────────────
    if action == "prices.get":
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
            gainers = _market_cache.get("top_gainers", [])
        await session.respond(req_id, True, data={
            "prices":      prices,
            "top_gainers": gainers[:10],
        })
        return

    # ── Trade history ─────────────────────────────────────────────────────────
    if action == "trade.history":
        mode = data.get("mode", "demo")
        if mode == "live":
            symbol     = data.get("symbol")
            local_log  = database.trade_log_get(session.user_id, limit=50)
            open_orders, hist_orders = [], []
            if bitget_client.keys_exist(session.user_id):
                orders_open = bitget_client.get_open_orders(session.user_id, symbol)
                orders_hist = bitget_client.get_order_history(session.user_id, symbol)
                open_orders = orders_open.get("orders", [])
                hist_orders = orders_hist.get("orders", [])
            # Merge local confirmed log with Bitget history (deduplicate by order_id)
            bitget_order_ids = {o.get("orderId", "") for o in hist_orders if o.get("orderId")}
            for lt in local_log:
                oid = lt.get("order_id", "")
                if not oid or oid not in bitget_order_ids:
                    # Normalise fields so frontend renders them consistently
                    hist_orders.append({
                        "orderId":    oid or lt.get("trade_id", ""),
                        "symbol":     lt.get("symbol", ""),
                        "side":       lt.get("side", ""),
                        "price":      lt.get("price") or 0,
                        "avgPrice":   lt.get("price") or 0,
                        "size":       lt.get("size", 0),
                        "status":     lt.get("status", "confirmed"),
                        "reason":     lt.get("reason", ""),
                        "executed_at": lt.get("executed_at", ""),
                        "_local":     True,
                    })
            await session.respond(req_id, True, data={
                "mode":    "live",
                "open":    open_orders,
                "history": hist_orders,
            })
        else:
            with _price_cache_lock:
                prices = {
                    sym.replace("USDT", ""): {"price": v["price"]}
                    for sym, v in _price_cache.items()
                }
            acc_raw = demo_trading.get_account(session.user_id)
            for pos in acc_raw.get("positions", []):
                sym_clean = pos["symbol"].replace("/USDT", "").replace("USDT", "")
                if sym_clean not in prices:
                    pd_ = get_cached_price(sym_clean + "USDT")
                    if pd_ and pd_.get("price"):
                        prices[sym_clean] = {"price": pd_["price"]}
            acc = demo_trading.get_positions_with_pnl(session.user_id, prices)
            await session.respond(req_id, True, data={
                "mode":          "demo",
                "positions":     acc.get("positions", []),
                "history":       acc.get("trade_history", []),
                "balance":       acc.get("balance_usdt", 0),
                "equity":        acc.get("equity", 0),
                "total_pnl":     acc.get("total_pnl", 0),
                "unrealised_pnl": acc.get("unrealised_pnl", 0),
                "win_rate":      acc.get("win_rate", 0),
                "total_trades":  acc.get("total_trades", 0),
            })
        return

    # ── Chat history list ─────────────────────────────────────────────────────
    if action == "chat.list":
        history = get_history_fn(session.user_id)
        msgs = [m for m in history.messages if m["role"] in ("user", "assistant")][-60:]
        await session.respond(req_id, True, data={"messages": msgs, "count": len(msgs)})
        return

    # ── User: change password ─────────────────────────────────────────────────
    if action == "user.change_password":
        old_pw = data.get("old_password", "")
        new_pw = data.get("new_password", "")
        if not old_pw or not new_pw:
            await session.respond(req_id, False, error="All fields required"); return
        if len(new_pw) < 6:
            await session.respond(req_id, False, error="New password must be at least 6 characters"); return
        user = auth_utils.get_user_by_id(session.user_id)
        if not user:
            await session.respond(req_id, False, error="User not found"); return
        if not auth_utils.verify_password(old_pw, user.get("pw_hash", "")):
            await session.respond(req_id, False, error="Current password is incorrect"); return
        result = auth_utils.update_password(user["email"], new_pw)
        await session.respond(req_id, result["ok"], error=result.get("error")); return

    # ── User: update username ─────────────────────────────────────────────────
    if action == "user.update_username":
        new_name = data.get("username", "")
        result = auth_utils.update_username(session.user_id, new_name)
        if result["ok"]:
            await session.respond(req_id, True, data={"username": result["username"]})
        else:
            await session.respond(req_id, False, error=result.get("error")); return

    # ── User: update avatar ───────────────────────────────────────────────────
    if action == "user.update_avatar":
        color  = data.get("color", "#00c853")
        emoji  = data.get("emoji", "")
        result = auth_utils.update_avatar(session.user_id, color, emoji)
        await session.respond(req_id, result["ok"], error=result.get("error")); return

    # ── Unknown ───────────────────────────────────────────────────────────────
    await session.respond(req_id, False, error=f"Unknown action: {action}")


# ── Helper: run LLM chat as async task ────────────────────────────────────────
import base64 as _b64
import os as _os


async def _run_chat_task(session: WsSession, user_input: str, get_history_fn, lock_fn, run_chat_fn):
    """Runs the LLM chat pipeline and streams responses over WS."""
    lock = lock_fn(session.user_id)
    await session.send({"type": "user", "content": user_input})
    async with lock:
        from main import WsStatusMsg
        status_msg = WsStatusMsg(session.ws)
        try:
            ai_response, chart_data_list, trade_setup_html, pending_trade = await run_chat_fn(
                session.user_id, user_input, status_msg
            )

            if pending_trade:
                await session.push("trade_confirm", {"data": pending_trade})

            if chart_data_list:
                await session.send({"type": "status", "content": ""})
                for cd in chart_data_list:
                    await session.send({
                        "type":     "chart_data",
                        "symbol":   cd.get("symbol", ""),
                        "interval": cd.get("interval", ""),
                        "candles":  cd.get("candles", []),
                        "price":    cd.get("price"),
                        "change":   cd.get("change", ""),
                        "label":    user_input[:40],
                    })

            if trade_setup_html:
                await session.send({"type": "trade_setup", "content": trade_setup_html})

            if ai_response:
                await session.send({"type": "assistant", "content": ai_response})
            elif not trade_setup_html and not chart_data_list:
                await session.send({"type": "assistant", "content": "I analyzed the data. Please check the market overview for the latest information, or ask me something more specific."})

        except asyncio.TimeoutError:
            await session.send({"type": "assistant", "content": "The request took too long. Please try again."})
        except Exception as e:
            await session.send({"type": "assistant", "content": f"An error occurred: {str(e)}"})
        await session.send({"type": "status", "content": ""})


def _send_trade_confirm(session, pending_trade):
    pass

"""
core/trade_manager.py — Pending Trade Lifecycle (SQLite-backed)
===============================================================
  • Creates trade proposals (agent → pending, NOT executed)
  • User confirms → executes via bitget_client
  • User rejects → removes pending
  • Auto-expire after TRADE_TIMEOUT_S seconds
"""
import time
import uuid
from datetime import datetime

import bitget_client
import database

TRADE_TIMEOUT_S = 300


def _load(user_id: str) -> dict:
    return database.trades_get(str(user_id))


def _save(user_id: str, data: dict):
    database.trades_save(str(user_id), data)


def create_pending(user_id: str, symbol: str, side: str, size: float,
                   price: float = None, sl: float = None, tp: float = None,
                   reason: str = "", entry_low: float = None, entry_high: float = None) -> dict:
    trade_id = str(uuid.uuid4())
    trade = {
        "trade_id":   trade_id,
        "user_id":    str(user_id),
        "symbol":     symbol.upper(),
        "side":       side.lower(),
        "size":       size,
        "price":      price,
        "entry_low":  entry_low,
        "entry_high": entry_high,
        "sl":         sl,
        "tp":         tp,
        "reason":     reason,
        "status":     "pending",
        "created_at": time.time(),
    }
    pending = _load(user_id)
    pending[trade_id] = trade
    _save(user_id, pending)
    return trade


def get_pending(user_id: str) -> list:
    pending = _load(user_id)
    now = time.time()
    active = []
    expired_ids = []
    for tid, t in pending.items():
        if now - t["created_at"] > TRADE_TIMEOUT_S:
            expired_ids.append(tid)
        else:
            active.append(t)
    if expired_ids:
        for tid in expired_ids:
            del pending[tid]
        _save(user_id, pending)
    return active


def confirm_trade(user_id: str, trade_id: str) -> dict:
    pending = _load(user_id)
    trade   = pending.get(trade_id)

    if not trade:
        return {"ok": False, "error": "Trade not found or already handled"}

    now = time.time()
    if now - trade["created_at"] > TRADE_TIMEOUT_S:
        del pending[trade_id]
        _save(user_id, pending)
        return {"ok": False, "error": "Trade proposal expired"}

    if trade["status"] != "pending":
        return {"ok": False, "error": f"Trade is already {trade['status']}"}

    result = bitget_client.place_order(
        user_id = user_id,
        symbol  = trade["symbol"],
        side    = trade["side"],
        size    = trade["size"],
        price   = trade.get("price"),
        sl      = trade.get("sl"),
        tp      = trade.get("tp"),
    )

    success = result.get("code") == "00000"
    trade["status"]      = "confirmed" if success else "failed"
    trade["executed_at"] = datetime.utcnow().isoformat()
    trade["result"]      = result

    del pending[trade_id]
    _save(user_id, pending)

    if success:
        order_data = result.get("data", {})
        order_id   = order_data.get("orderId", "")
        # ── Save to local confirmed trade log so history persists without Bitget API ──
        trade["order_id"] = order_id
        database.trade_log_save(user_id, trade, order_id=order_id)
        return {
            "ok":       True,
            "order_id": order_id,
            "trade":    trade,
            "message":  f"✅ Order placed: {trade['side'].upper()} {trade['size']} {trade['symbol']}",
        }
    return {
        "ok":    False,
        "error": result.get("msg", "Order placement failed"),
        "trade": trade,
    }


def reject_trade(user_id: str, trade_id: str) -> dict:
    pending = _load(user_id)
    if trade_id not in pending:
        return {"ok": False, "error": "Trade not found"}
    del pending[trade_id]
    _save(user_id, pending)
    return {"ok": True, "message": "❌ Trade rejected"}


def cleanup_expired_all():
    now = time.time()
    for user_id in database.trades_get_all_user_ids():
        pending = _load(user_id)
        changed = False
        for tid in list(pending.keys()):
            if now - pending[tid]["created_at"] > TRADE_TIMEOUT_S:
                del pending[tid]
                changed = True
        if changed:
            _save(user_id, pending)

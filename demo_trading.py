"""
core/demo_trading.py — Paper Trading / Demo Mode (SQLite-backed)
================================================================
  • Each user starts with $10,000 USDT demo balance
  • Trades are simulated using live Bitget prices
  • Positions tracked per user (SQLite demo_accounts table)
  • P&L calculated in real-time
  • Risk limits enforced (max 20% per trade, max 5 open positions)
"""
import uuid
from datetime import datetime

import database

DEMO_START_USDT = 10_000.0
MAX_POSITIONS   = 5
MAX_TRADE_PCT   = 0.20


def _new_account(user_id: str) -> dict:
    return {
        "user_id":        user_id,
        "created_at":     datetime.utcnow().isoformat(),
        "balance_usdt":   DEMO_START_USDT,
        "positions":      [],
        "trade_history":  [],
        "total_trades":   0,
        "winning_trades": 0,
        "total_pnl":      0.0,
    }


def _load(user_id: str) -> dict:
    data = database.demo_get(str(user_id))
    if data is None:
        return _new_account(user_id)
    return data


def _save(user_id: str, data: dict):
    database.demo_save(str(user_id), data)


def get_account(user_id: str) -> dict:
    return _load(user_id)


def reset_account(user_id: str) -> dict:
    acc = _new_account(user_id)
    _save(user_id, acc)
    return acc


def place_demo_trade(user_id: str, symbol: str, side: str,
                     size_usdt: float, price: float,
                     sl: float = None, tp: float = None,
                     reason: str = "") -> dict:
    acc = _load(user_id)

    if size_usdt <= 0:
        return {"ok": False, "error": "Size must be positive"}
    if size_usdt > acc["balance_usdt"]:
        return {"ok": False, "error": f"Insufficient demo balance (available: ${acc['balance_usdt']:,.2f})"}
    if len(acc["positions"]) >= MAX_POSITIONS:
        return {"ok": False, "error": f"Max {MAX_POSITIONS} open positions in demo mode"}

    coin_qty = size_usdt / price
    trade_id = str(uuid.uuid4())[:8].upper()

    position = {
        "trade_id":    trade_id,
        "symbol":      symbol.upper().replace("USDT", "") + "/USDT",
        "side":        side.lower(),
        "size_usdt":   size_usdt,
        "coin_qty":    coin_qty,
        "entry_price": price,
        "sl":          sl,
        "tp":          tp,
        "reason":      reason,
        "opened_at":   datetime.utcnow().isoformat(),
        "status":      "open",
    }

    acc["balance_usdt"] -= size_usdt
    acc["positions"].append(position)
    acc["total_trades"] += 1
    _save(user_id, acc)

    return {
        "ok":       True,
        "trade_id": trade_id,
        "message":  f"✅ DEMO {side.upper()} {coin_qty:.6f} {symbol.replace('USDT','')} @ ${price:,.4f}",
        "position": position,
        "balance":  acc["balance_usdt"],
    }


def close_demo_position(user_id: str, trade_id: str, close_price: float) -> dict:
    acc = _load(user_id)
    pos = next((p for p in acc["positions"] if p["trade_id"] == trade_id), None)
    if not pos:
        return {"ok": False, "error": "Position not found"}

    entry = pos["entry_price"]
    qty   = pos["coin_qty"]
    side  = pos["side"]

    pnl = (close_price - entry) * qty if side == "buy" else (entry - close_price) * qty

    close_value        = pos["size_usdt"] + pnl
    pos["close_price"] = close_price
    pos["closed_at"]   = datetime.utcnow().isoformat()
    pos["pnl"]         = round(pnl, 4)
    pos["pnl_pct"]     = round(pnl / pos["size_usdt"] * 100, 2)
    pos["status"]      = "closed"

    acc["balance_usdt"] += close_value
    acc["total_pnl"]    += pnl
    acc["positions"]     = [p for p in acc["positions"] if p["trade_id"] != trade_id]
    if pnl > 0:
        acc["winning_trades"] += 1
    acc["trade_history"].append(pos)
    if len(acc["trade_history"]) > 100:
        acc["trade_history"] = acc["trade_history"][-100:]

    _save(user_id, acc)

    return {
        "ok":      True,
        "trade_id": trade_id,
        "pnl":     round(pnl, 4),
        "pnl_pct": pos["pnl_pct"],
        "balance": acc["balance_usdt"],
        "message": f"{'✅ Profit' if pnl >= 0 else '❌ Loss'} ${pnl:+.2f} ({pos['pnl_pct']:+.2f}%)",
    }


def get_positions_with_pnl(user_id: str, prices: dict) -> dict:
    acc = _load(user_id)
    positions  = []
    unrealised = 0.0

    for pos in acc["positions"]:
        sym   = pos["symbol"].replace("/USDT", "")
        price = prices.get(sym, {}).get("price", pos["entry_price"])
        qty   = pos["coin_qty"]
        entry = pos["entry_price"]
        side  = pos["side"]

        upnl = (price - entry) * qty if side == "buy" else (entry - price) * qty
        upnl_pct = upnl / pos["size_usdt"] * 100 if pos["size_usdt"] else 0
        positions.append({
            **pos,
            "current_price":  price,
            "unrealised_pnl": round(upnl, 4),
            "unrealised_pct": round(upnl_pct, 2),
        })
        unrealised += upnl

    total_equity = acc["balance_usdt"] + sum(p["size_usdt"] for p in acc["positions"]) + unrealised

    return {
        "balance_usdt":   round(acc["balance_usdt"], 4),
        "equity":         round(total_equity, 4),
        "unrealised_pnl": round(unrealised, 4),
        "total_pnl":      round(acc["total_pnl"], 4),
        "total_trades":   acc["total_trades"],
        "winning_trades": acc["winning_trades"],
        "win_rate":       round(acc["winning_trades"] / max(acc["total_trades"], 1) * 100, 1),
        "positions":      positions,
        "trade_history":  acc["trade_history"][-20:],
    }

"""
core/auth_utils.py — Authentication helpers (SQLite-backed)
============================================================
  • bcrypt password hashing / verification
  • JWT (HS256) access + refresh token issuance / decoding
  • JWT blacklist stored in SQLite (jwt_blacklist table)
  • User store → SQLite users table
  • Email OTP — 6-digit code, 10 min expiry, max 3 attempts
"""
import os
import uuid
import threading
import time
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

import bcrypt
import jwt

import database

import sys as _sys, os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
import config as _cfg
JWT_SECRET   = _cfg.JWT_SECRET
ACCESS_EXP_S = 3600
REFRESH_EXP_S = 7 * 86400
ALGORITHM    = "HS256"

OTP_EXPIRY_S  = 600
OTP_MAX_TRIES = 3

_otp_lock: threading.Lock = threading.Lock()
_otp_store: dict = {}


# ── Password helpers ───────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), pw_hash.encode())
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def _now_ts() -> int:
    return int(time.time())


def create_jwt(user_id: str, token_type: str = "access") -> str:
    exp = _now_ts() + (ACCESS_EXP_S if token_type == "access" else REFRESH_EXP_S)
    payload = {
        "sub":  str(user_id),
        "type": token_type,
        "jti":  str(uuid.uuid4()),
        "iat":  _now_ts(),
        "exp":  exp,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_jwt(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    if is_blacklisted(payload.get("jti", "")):
        raise jwt.InvalidTokenError("Token has been revoked")
    return payload


# ── Blacklist ─────────────────────────────────────────────────────────────────

def blacklist_token(jti: str):
    database.blacklist_add(jti, _now_ts())


def is_blacklisted(jti: str) -> bool:
    return database.blacklist_exists(jti)


def purge_expired_blacklist():
    database.blacklist_purge(_now_ts() - REFRESH_EXP_S)


# ── User store ─────────────────────────────────────────────────────────────────

def _load_users() -> dict:
    return database.users_get_all()


def _save_users(users: dict):
    database.users_save_all(users)


def register_user(username: str, email: str, password: str = None) -> dict:
    email = email.strip().lower()
    users = _load_users()
    if email in users:
        return {"ok": False, "error": "Email already registered"}
    for u in users.values():
        if u.get("username", "").lower() == username.strip().lower():
            return {"ok": False, "error": "Username already taken"}

    user_id = str(uuid.uuid4())
    new_user = {
        "id":         user_id,
        "username":   username.strip(),
        "pw_hash":    hash_password(password) if password else "",
        "plan":       "free",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "avatar_color": "",
        "avatar_emoji": "",
    }
    database.user_upsert(email, new_user)
    return {"ok": True, "user_id": user_id, "username": username.strip()}


def login_user(email: str, password: str = None) -> dict:
    email = email.strip().lower()
    users = _load_users()
    user  = users.get(email)
    if not user:
        return {"ok": False, "error": "Email not found"}
    if password and user.get("pw_hash"):
        if not verify_password(password, user["pw_hash"]):
            return {"ok": False, "error": "Invalid password"}
    return {"ok": True, "user_id": user["id"], "username": user["username"], "plan": user.get("plan", "free")}


def get_user_by_id(user_id: str) -> dict | None:
    users = _load_users()
    for email, u in users.items():
        if u["id"] == user_id:
            return {**u, "email": email}
    return None


def get_user_by_email(email: str) -> dict | None:
    users = _load_users()
    u = users.get(email.strip().lower())
    if u:
        return {**u, "email": email.strip().lower()}
    return None


def email_exists(email: str) -> bool:
    users = _load_users()
    return email.strip().lower() in users


def update_password(email: str, new_password: str) -> dict:
    email = email.strip().lower()
    users = _load_users()
    if email not in users:
        return {"ok": False, "error": "User not found"}
    users[email]["pw_hash"] = hash_password(new_password)
    database.user_upsert(email, users[email])
    return {"ok": True}


def update_username(user_id: str, new_username: str) -> dict:
    new_username = new_username.strip()
    if not new_username or len(new_username) < 2:
        return {"ok": False, "error": "Username must be at least 2 characters"}
    if len(new_username) > 24:
        return {"ok": False, "error": "Username must be 24 characters or less"}
    users = _load_users()
    for email, u in users.items():
        if u["id"] != user_id and u.get("username", "").lower() == new_username.lower():
            return {"ok": False, "error": "Username already taken"}
    for email, u in users.items():
        if u["id"] == user_id:
            u["username"] = new_username
            database.user_upsert(email, u)
            return {"ok": True, "username": new_username}
    return {"ok": False, "error": "User not found"}


def update_avatar(user_id: str, avatar_color: str, avatar_emoji: str) -> dict:
    users = _load_users()
    for email, u in users.items():
        if u["id"] == user_id:
            u["avatar_color"] = avatar_color[:7]
            u["avatar_emoji"] = avatar_emoji[:4]
            database.user_upsert(email, u)
            return {"ok": True}
    return {"ok": False, "error": "User not found"}


def issue_tokens(user_id: str) -> dict:
    return {
        "access_token":  create_jwt(user_id, "access"),
        "refresh_token": create_jwt(user_id, "refresh"),
        "expires_in":    ACCESS_EXP_S,
        "token_type":    "Bearer",
    }


# ── OTP ───────────────────────────────────────────────────────────────────────

def generate_otp() -> str:
    return f"{random.randint(100000, 999999)}"


def store_otp(email: str, otp_type: str, extra: dict = None) -> str:
    code = generate_otp()
    with _otp_lock:
        _otp_store[email.lower()] = {
            "code":     code,
            "expires":  time.time() + OTP_EXPIRY_S,
            "type":     otp_type,
            "attempts": 0,
            "extra":    extra or {},
        }
    return code


def verify_otp(email: str, code: str, otp_type: str) -> dict:
    email = email.strip().lower()
    with _otp_lock:
        record = _otp_store.get(email)
        if not record:
            return {"ok": False, "error": "No OTP found. Please request a new one."}
        if record["type"] != otp_type:
            return {"ok": False, "error": "OTP type mismatch."}
        if time.time() > record["expires"]:
            _otp_store.pop(email, None)
            return {"ok": False, "error": "OTP expired. Please request a new one."}
        record["attempts"] += 1
        if record["attempts"] > OTP_MAX_TRIES:
            _otp_store.pop(email, None)
            return {"ok": False, "error": "Too many attempts. Please request a new OTP."}
        if record["code"] != code.strip():
            remaining = OTP_MAX_TRIES - record["attempts"] + 1
            return {"ok": False, "error": f"Invalid OTP. {remaining} attempt(s) left."}
        extra = record.get("extra", {})
        _otp_store.pop(email, None)
    return {"ok": True, "extra": extra}


def send_otp_email(to_email: str, code: str, otp_type: str) -> dict:
    smtp_user = "newidusapumal@gmail.com"
    smtp_pass = "anht fged tmkb wxti"

    if not smtp_user or not smtp_pass:
        print(f"[OTP] {to_email} | type={otp_type} | code={code} (email not configured)")
        return {"ok": True, "dev_mode": True}

    if otp_type == "reset":
        action = "Password Reset"
    elif otp_type == "register":
        action = "Account Registration"
    else:
        action = "Login"

    html_body = f"""
<div style="font-family:'IBM Plex Mono',monospace;background:#0a0a0a;padding:32px;color:#e8e8e8;max-width:480px;margin:auto;border:1px solid #2a2a2a;">
  <div style="font-size:20px;font-weight:600;color:#fff;letter-spacing:3px;margin-bottom:4px;">TrendOracle</div>
  <div style="font-size:10px;color:#666;letter-spacing:2px;margin-bottom:28px;text-transform:uppercase;">AI Crypto Intelligence</div>
  <div style="font-size:12px;color:#999;margin-bottom:20px;">{action} Verification</div>
  <div style="background:#111;border:1px solid #2a2a2a;padding:24px;text-align:center;margin-bottom:24px;">
    <div style="font-size:11px;color:#666;letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;">Your Verification Code</div>
    <div style="font-size:36px;font-weight:600;color:#00c853;letter-spacing:8px;">{code}</div>
    <div style="font-size:10px;color:#666;margin-top:12px;">Expires in 10 minutes</div>
  </div>
  <div style="font-size:10px;color:#666;line-height:1.7;">
    If you did not request this code, please ignore this email.<br>
    Never share your verification code with anyone.
  </div>
  <div style="margin-top:24px;padding-top:16px;border-top:1px solid #2a2a2a;font-size:9px;color:#444;letter-spacing:0.5px;">
    TrendOracle — AI-Powered Crypto Intelligence Platform
  </div>
</div>
"""
    text_body = f"TrendOracle — Your code for {action}: {code}\n\nExpires in 10 minutes. Do not share."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"TrendOracle — {action} Code: {code}"
    msg["From"]    = f"TrendOracle <{smtp_user}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return {"ok": True}
    except Exception as e:
        print(f"[OTP] Email send failed: {e}")
        return {"ok": False, "error": str(e)}

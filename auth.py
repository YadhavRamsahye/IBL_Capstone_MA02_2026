"""
auth.py
Authentication and route protection.

What this replaces
------------------
Login previously compared against a plaintext dict in main.py:

    DEMO_USERS = {"admin": "admin123", "user": "password"}

while seed_users.py correctly bcrypt-hashed the same accounts into a `users`
table that nothing ever read. Signup validated its inputs, wrote nothing, and
redirected with "Account created successfully" — a message that was simply
untrue. And every /api/* route was unauthenticated, so the login page guarded
the HTML pages while the data behind them was open to anyone.

This module provides the missing pieces: bcrypt verification against the users
table, real account creation, and a dependency that routes can require.

Fallback behaviour
------------------
With no database reachable the app still needs to be demonstrable, so a
built-in admin account is available — but only when DEMO_LOGIN_ENABLED is true
*and* the database is genuinely unavailable, and it logs a warning every time
it is used so it cannot be mistaken for real authentication. Set
ALLOW_DEMO_LOGIN=false in the environment to disable it entirely.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import text

from database import DB_AVAILABLE, AsyncSessionLocal as _AsyncSession

logger = logging.getLogger(__name__)

# Opt-in, not opt-out: DB_AVAILABLE is decided once at process startup (was
# DB_PASSWORD set, was Postgres reachable via a TCP probe at that moment, did
# the engine come up) and stays fixed for the process's whole life, so any
# deployment that happens to boot with the database briefly unreachable - or
# simply hasn't run Phase 7 setup yet - silently gained an admin account
# using a well-known default password for as long as it kept running. Whoever
# wants this fallback for a demo has to say so explicitly.
DEMO_LOGIN_ENABLED = os.getenv("ALLOW_DEMO_LOGIN", "false").lower() == "true"
_DEMO_USER = "admin"
_DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "admin123")

MIN_USERNAME_LEN = 3
MIN_PASSWORD_LEN = 8
EMAIL_MAX_LEN = 255


def get_session_secret() -> str:
    """Session signing key, from the environment.

    The key was previously the literal string
    "traffic-mauritius-secret-key-2026", committed to git — anyone with the
    repository could forge an admin session cookie. A random key is generated
    if SESSION_SECRET is unset so the app still runs, at the cost of
    invalidating sessions on restart; set SESSION_SECRET in .env to persist them.
    """
    secret = os.getenv("SESSION_SECRET")
    if secret:
        return secret
    logger.warning(
        "SESSION_SECRET is not set — generating a random key. Sessions will not "
        "survive a restart. Add SESSION_SECRET to your .env file."
    )
    return secrets.token_urlsafe(48)


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        # Malformed/truncated hash in the database — treat as a failed login
        # rather than a 500.
        logger.warning("Malformed password hash encountered during verification.")
        return False


def _is_valid_email(value: str) -> bool:
    if not value or "@" not in value:
        return False
    local, _, domain = value.rpartition("@")
    return bool(local and domain and "." in domain and len(value) <= EMAIL_MAX_LEN)


async def create_password_reset_token(email: str, expires_in: int = 3600) -> Optional[str]:
    if not DB_AVAILABLE or _AsyncSession is None:
        return None
    email = email.strip().lower()
    if not _is_valid_email(email):
        return None
    try:
        async with _AsyncSession() as db:
            row = (await db.execute(
                text("SELECT id FROM users WHERE email = :e"),
                {"e": email},
            )).mappings().first()
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            await db.execute(
                text("INSERT INTO password_reset_tokens "
                     "(token, user_id, expires_at, used) "
                     "VALUES (:token, :user_id, :expires_at, false)"),
                {
                    "token": token,
                    "user_id": str(row["id"]),
                    "expires_at": datetime.now(timezone.utc)
                                   + timedelta(seconds=expires_in),
                },
            )
            await db.commit()
            return token
    except Exception as exc:
        logger.error("Password reset token creation failed: %s", exc, exc_info=True)
        return None


async def get_password_reset_user(token: str) -> Optional[dict]:
    if not DB_AVAILABLE or _AsyncSession is None:
        return None
    if not token:
        return None
    try:
        async with _AsyncSession() as db:
            row = (await db.execute(
                text("SELECT users.id AS user_id, users.username, users.email "
                     "FROM password_reset_tokens "
                     "JOIN users ON users.id = password_reset_tokens.user_id "
                     "WHERE password_reset_tokens.token = :token "
                     "AND password_reset_tokens.used = false "
                     "AND password_reset_tokens.expires_at > NOW()"),
                {"token": token},
            )).mappings().first()
            if row:
                return {
                    "id": str(row["user_id"]),
                    "username": row["username"],
                    "email": row["email"],
                }
    except Exception as exc:
        logger.error("Password reset token validation failed: %s", exc, exc_info=True)
    return None


async def reset_password(token: str, password: str) -> tuple[bool, str]:
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if not DB_AVAILABLE or _AsyncSession is None:
        return False, ("Password reset is unavailable — the database is not "
                       "connected. Contact an administrator.")
    try:
        async with _AsyncSession() as db:
            row = (await db.execute(
                text("SELECT user_id FROM password_reset_tokens "
                     "WHERE token = :token "
                     "AND used = false "
                     "AND expires_at > NOW()"),
                {"token": token},
            )).mappings().first()
            if not row:
                return False, "Invalid or expired password reset token."
            await db.execute(
                text("UPDATE users SET password_hash = :h WHERE id = :user_id"),
                {"h": hash_password(password), "user_id": str(row["user_id"])},
            )
            await db.execute(
                text("UPDATE password_reset_tokens SET used = true WHERE token = :token"),
                {"token": token},
            )
            await db.commit()
            return True, "Password reset successfully. Please sign in."
    except Exception as exc:
        logger.error("Password reset failed: %s", exc, exc_info=True)
        return False, "Could not reset the password. Please try again."


# ── User store ────────────────────────────────────────────────────────────────

async def authenticate(username: str, password: str) -> Optional[dict]:
    """Return the user record on success, or None. Never raises on bad input."""
    if DB_AVAILABLE and _AsyncSession is not None:
        try:
            async with _AsyncSession() as db:
                login_value = username.strip()
                if _is_valid_email(login_value):
                    login_value = login_value.lower()
                    row = (await db.execute(
                        text("SELECT id, username, password_hash, role FROM users "
                             "WHERE email = :u"),
                        {"u": login_value},
                    )).mappings().first()
                else:
                    row = (await db.execute(
                        text("SELECT id, username, password_hash, role FROM users "
                             "WHERE username = :u"),
                        {"u": login_value},
                    )).mappings().first()

            if row and verify_password(password, row["password_hash"]):
                await _touch_last_login(row["username"])
                return {"username": row["username"], "role": row["role"],
                        "id": str(row["id"])}
            return None
        except Exception as exc:
            logger.error("Authentication query failed: %s", exc, exc_info=True)
            return None

    # No database — fall back to the demo account if permitted.
    if DEMO_LOGIN_ENABLED and username == _DEMO_USER and \
            secrets.compare_digest(password, _DEMO_PASSWORD):
        logger.warning(
            "Demo login used for '%s' — no database is configured, so this is "
            "NOT real authentication. Set ALLOW_DEMO_LOGIN=false to disable.",
            username,
        )
        return {"username": _DEMO_USER, "role": "admin", "id": "demo"}
    return None


async def create_user(username: str, email: str, password: str, role: str = "user") -> tuple[bool, str]:
    """Create an account. Returns (ok, message)."""
    username = username.strip()
    email = email.strip().lower()
    if len(username) < MIN_USERNAME_LEN:
        return False, f"Username must be at least {MIN_USERNAME_LEN} characters."
    if not _is_valid_email(email):
        return False, "Enter a valid email address."
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if not DB_AVAILABLE or _AsyncSession is None:
        # Say so plainly instead of the old silent success.
        return False, ("Account creation is unavailable — the database is not "
                       "connected. Contact an administrator.")
    try:
        async with _AsyncSession() as db:
            existing = (await db.execute(
                text("SELECT 1 FROM users WHERE username = :u OR email = :e"),
                {"u": username, "e": email}
            )).first()
            if existing:
                return False, "That username or email is already taken."
            await db.execute(
                text("INSERT INTO users (id, username, email, password_hash, role) "
                     "VALUES (:id, :u, :e, :h, :r)"),
                {"id": str(uuid.uuid4()), "u": username,
                 "e": email, "h": hash_password(password), "r": role},
            )
            await db.commit()
        return True, "Account created successfully. Please sign in."
    except Exception as exc:
        logger.error("User creation failed: %s", exc, exc_info=True)
        return False, "Could not create the account. Please try again."


async def _touch_last_login(username: str) -> None:
    try:
        async with _AsyncSession() as db:
            await db.execute(
                text("UPDATE users SET last_login = NOW() WHERE username = :u"),
                {"u": username},
            )
            await db.commit()
    except Exception:
        logger.debug("last_login update failed for %s", username, exc_info=True)


async def record_audit(request: Request, action: str, username: str,
                       success: bool, target: str = "") -> None:
    """Best-effort audit trail. The audit_log table existed but nothing wrote to it."""
    if not DB_AVAILABLE or _AsyncSession is None:
        return
    try:
        async with _AsyncSession() as db:
            await db.execute(
                text("INSERT INTO audit_log (user_id, username, action, target, "
                     "ip_address, success) VALUES ("
                     "(SELECT id FROM users WHERE username = :u), :u, :a, :t, :ip, :s)"),
                {"u": username, "a": action, "t": target or None,
                 "ip": request.client.host if request.client else None,
                 "s": success},
            )
            await db.commit()
    except Exception:
        logger.debug("Audit write failed for %s/%s", username, action, exc_info=True)


# ── Route protection ──────────────────────────────────────────────────────────

def get_current_user(request: Request) -> Optional[dict]:
    """Session user, or None. Use for pages that redirect when signed out."""
    return request.session.get("user")


async def require_user(request: Request) -> dict:
    """FastAPI dependency: 401 unless a session exists.

    Apply to every data route. Previously only the HTML pages checked the
    session, so /api/traffic, /api/cameras and the alert-trigger endpoint were
    readable and writable by anyone who knew the URL.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user


async def require_admin(user: dict = Depends(require_user)) -> dict:
    """FastAPI dependency: 403 unless the session user is an admin."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required.",
        )
    return user


async def websocket_user(websocket: WebSocket) -> Optional[dict]:
    """Session user for a WebSocket, or None.

    SessionMiddleware populates `scope["session"]` for WebSocket connections
    too, so the same cookie that authorises the page authorises the socket.
    """
    return websocket.scope.get("session", {}).get("user")

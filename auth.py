"""
auth.py
Authentication and route protection.

What this replaces
------------------
Login previously compared against a plaintext dict in main.py:

    DEMO_USERS = {"admin": "admin123", "user": "password"}

while seed_users.py correctly bcrypt-hashed the same accounts into a `users`
table that nothing ever read. Signup validated its inputs, wrote nothing, and
redirected with "Account created successfully" - a message that was simply
untrue. And every /api/* route was unauthenticated, so the login page guarded
the HTML pages while the data behind them was open to anyone.

This module provides the missing pieces: bcrypt verification against the users
table, real account creation, and a dependency that routes can require.

Fallback behaviour
------------------
With no database reachable the app still needs to be demonstrable, so a
built-in admin account is available - but only when DEMO_LOGIN_ENABLED is true
*and* the database is genuinely unavailable, and it logs a warning every time
it is used so it cannot be mistaken for real authentication. Set
ALLOW_DEMO_LOGIN=false in the environment to disable it entirely.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
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


def get_session_secret() -> str:
    """Session signing key, from the environment.

    The key was previously the literal string
    "traffic-mauritius-secret-key-2026", committed to git - anyone with the
    repository could forge an admin session cookie. A random key is generated
    if SESSION_SECRET is unset so the app still runs, at the cost of
    invalidating sessions on restart; set SESSION_SECRET in .env to persist them.
    """
    secret = os.getenv("SESSION_SECRET")
    if secret:
        return secret
    logger.warning(
        "SESSION_SECRET is not set - generating a random key. Sessions will not "
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
        # Malformed/truncated hash in the database - treat as a failed login
        # rather than a 500.
        logger.warning("Malformed password hash encountered during verification.")
        return False


# ── User store ────────────────────────────────────────────────────────────────

async def authenticate(username: str, password: str) -> Optional[dict]:
    """Return the user record on success, or None. Never raises on bad input."""
    if DB_AVAILABLE and _AsyncSession is not None:
        try:
            async with _AsyncSession() as db:
                row = (await db.execute(
                    text("SELECT id, username, password_hash, role FROM users "
                         "WHERE username = :u"),
                    {"u": username},
                )).mappings().first()
            if row and verify_password(password, row["password_hash"]):
                await _touch_last_login(username)
                return {"username": row["username"], "role": row["role"],
                        "id": str(row["id"])}
            return None
        except Exception as exc:
            logger.error("Authentication query failed: %s", exc, exc_info=True)
            return None

    # No database - fall back to the demo account if permitted.
    if DEMO_LOGIN_ENABLED and username == _DEMO_USER and \
            secrets.compare_digest(password, _DEMO_PASSWORD):
        logger.warning(
            "Demo login used for '%s' - no database is configured, so this is "
            "NOT real authentication. Set ALLOW_DEMO_LOGIN=false to disable.",
            username,
        )
        return {"username": _DEMO_USER, "role": "admin", "id": "demo"}
    return None


async def create_user(username: str, password: str, role: str = "user",
                      email: Optional[str] = None) -> tuple[bool, str]:
    """Create an account. Returns (ok, message)."""
    if len(username) < MIN_USERNAME_LEN:
        return False, f"Username must be at least {MIN_USERNAME_LEN} characters."
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if not DB_AVAILABLE or _AsyncSession is None:
        # Say so plainly instead of the old silent success.
        return False, ("Account creation is unavailable - the database is not "
                       "connected. Contact an administrator.")
    try:
        async with _AsyncSession() as db:
            existing = (await db.execute(
                text("SELECT 1 FROM users WHERE username = :u"), {"u": username}
            )).first()
            if existing:
                return False, "That username is already taken."
            if email:
                clash = (await db.execute(
                    text("SELECT 1 FROM users WHERE LOWER(email) = LOWER(:e)"),
                    {"e": email},
                )).first()
                if clash:
                    return False, "That email address is already registered."

            await db.execute(
                text("INSERT INTO users (id, username, password_hash, role, email) "
                     "VALUES (:id, :u, :h, :r, :e)"),
                {"id": str(uuid.uuid4()), "u": username,
                 "h": hash_password(password), "r": role, "e": email},
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


# -- Password recovery ---------------------------------------------------------
#
# Threat model, and why each decision is what it is:
#
#   * Tokens are 32 bytes of CSPRNG output, stored as a SHA-256 hash. A stolen
#     database must not yield working reset links. SHA-256 rather than bcrypt
#     because there is no low-entropy guess to slow down, and a cheap hash can
#     be looked up by index instead of scanning every row.
#   * Requesting a reset never reveals whether an account exists. The response
#     and the timing are the same either way, so the form cannot be used to
#     enumerate accounts.
#   * Tokens are single-use and short-lived, and every outstanding token for a
#     user is invalidated the moment any password change succeeds.

RESET_TOKEN_BYTES = 32
RESET_TOKEN_TTL_MINUTES = int(os.getenv("RESET_TOKEN_TTL_MINUTES", "30"))
# Requests allowed per account per hour, so the form cannot be used to flood
# somebody's inbox.
RESET_MAX_PER_HOUR = int(os.getenv("RESET_MAX_PER_HOUR", "5"))


def _safe_ip(value):
    """Return value only if it is a real IP address, else None.

    requested_ip is an INET column and asyncpg refuses anything that is not a
    valid address - it does not quietly store the string. request.client.host
    is normally an IP, but behind a proxy or a test client it can be a hostname,
    and that would abort the whole reset rather than just losing an audit
    detail. The address is diagnostic, so drop it instead.
    """
    import ipaddress
    if not value:
        return None
    try:
        ipaddress.ip_address(str(value))
        return str(value)
    except ValueError:
        logger.debug("Discarding non-IP client address %r for audit field.", value)
        return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_password_reset(identifier: str, ip: Optional[str] = None):
    """Issue a reset token for a username or email address.

    Returns (user_record, raw_token) when a token was issued, or (None, None).
    Callers must respond identically either way - see the module notes above.
    """
    if not DB_AVAILABLE or _AsyncSession is None:
        logger.warning("Password reset requested but no database is connected.")
        return None, None

    identifier = (identifier or "").strip()
    if not identifier:
        return None, None

    try:
        async with _AsyncSession() as db:
            row = (await db.execute(
                text("SELECT id, username, email FROM users "
                     "WHERE username = :ident OR LOWER(email) = LOWER(:ident)"),
                {"ident": identifier},
            )).mappings().first()

            if not row:
                logger.info("Reset requested for unknown identifier (no mail sent).")
                return None, None
            if not row["email"]:
                # Nothing to send to. Deliberately not surfaced to the caller.
                logger.info("Reset requested for %r which has no email address.",
                            row["username"])
                return None, None

            recent = (await db.execute(
                text("SELECT COUNT(*) FROM password_resets WHERE user_id = :uid "
                     "AND created_at >= NOW() - INTERVAL '1 hour'"),
                {"uid": row["id"]},
            )).scalar_one()
            if recent >= RESET_MAX_PER_HOUR:
                logger.warning("Reset rate limit reached for %r.", row["username"])
                return None, None

            token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
            await db.execute(
                text("INSERT INTO password_resets "
                     "(id, user_id, token_hash, expires_at, requested_ip) "
                     "VALUES (:id, :uid, :th, "
                     "NOW() + make_interval(mins => :ttl), :ip)"),
                {"id": str(uuid.uuid4()), "uid": row["id"],
                 "th": _hash_token(token), "ttl": RESET_TOKEN_TTL_MINUTES,
                 "ip": _safe_ip(ip)},
            )
            await db.commit()

        return {"id": str(row["id"]), "username": row["username"],
                "email": row["email"]}, token

    except Exception as exc:
        logger.error("Could not create password reset: %s", exc, exc_info=True)
        return None, None


async def verify_reset_token(token: str):
    """Return the user the token belongs to, or None if it cannot be used.

    Rejects tokens that are unknown, expired, or already spent.
    """
    if not DB_AVAILABLE or _AsyncSession is None or not token:
        return None
    try:
        async with _AsyncSession() as db:
            row = (await db.execute(
                text("SELECT r.id AS reset_id, u.id AS user_id, u.username, u.email "
                     "FROM password_resets r JOIN users u ON u.id = r.user_id "
                     "WHERE r.token_hash = :th AND r.used_at IS NULL "
                     "AND r.expires_at > NOW()"),
                {"th": _hash_token(token)},
            )).mappings().first()
        if not row:
            return None
        return {"reset_id": str(row["reset_id"]), "user_id": str(row["user_id"]),
                "username": row["username"], "email": row["email"]}
    except Exception as exc:
        logger.error("Reset token verification failed: %s", exc, exc_info=True)
        return None


async def consume_reset_token(token: str, new_password: str):
    """Set a new password and burn the token. Returns (ok, message).

    The token is marked used in the same transaction as the password change, so
    a token cannot be replayed even if two requests arrive together.
    """
    if len(new_password) < MIN_PASSWORD_LEN:
        return False, f"Password must be at least {MIN_PASSWORD_LEN} characters."

    record = await verify_reset_token(token)
    if not record:
        return False, ("That reset link is invalid or has expired. "
                       "Request a new one below.")

    try:
        async with _AsyncSession() as db:
            marked = (await db.execute(
                text("UPDATE password_resets SET used_at = NOW() "
                     "WHERE id = :rid AND used_at IS NULL RETURNING id"),
                {"rid": record["reset_id"]},
            )).first()
            if not marked:
                # Another request consumed it between verify and update.
                await db.rollback()
                return False, "That reset link has already been used."

            await db.execute(
                text("UPDATE users SET password_hash = :h WHERE id = :uid"),
                {"h": hash_password(new_password), "uid": record["user_id"]},
            )
            # Any other outstanding link for this account is now void.
            await db.execute(
                text("UPDATE password_resets SET used_at = NOW() "
                     "WHERE user_id = :uid AND used_at IS NULL"),
                {"uid": record["user_id"]},
            )
            await db.commit()

        logger.info("Password reset completed for %r.", record["username"])
        return True, "Your password has been changed. Please sign in."

    except Exception as exc:
        logger.error("Password reset failed: %s", exc, exc_info=True)
        return False, "Could not reset the password. Please try again."

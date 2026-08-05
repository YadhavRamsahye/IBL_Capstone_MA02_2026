"""
Author : Mokshan Mehess (22703417) — Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger(__name__)

HOST     = os.getenv("DB_HOST",     "localhost")
PORT     = int(os.getenv("DB_PORT", "5432"))
USER     = os.getenv("DB_USER",     "postgres")
PASSWORD = os.getenv("DB_PASSWORD", "tqu9vfds")
DB_NAME  = os.getenv("DB_NAME",     "TrafficSystem")

DATABASE_URL = f"postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB_NAME}"

DB_AVAILABLE: bool = False
engine = None
AsyncSessionLocal = None


def _postgres_reachable() -> bool:
    """TCP-level check so we fail fast instead of waiting for SQLAlchemy's lazy connect."""
    try:
        with socket.create_connection((HOST, PORT), timeout=3):
            return True
    except OSError:
        return False


if not _postgres_reachable():
    logger.warning(
        "[db] PostgreSQL not reachable at %s:%d — starting without persistence. "
        "Set DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME to connect.",
        HOST, PORT,
    )
else:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker

        engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        AsyncSessionLocal = sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False,
        )
        DB_AVAILABLE = True
        logger.info("[db] Engine ready → %s:%d/%s", HOST, PORT, DB_NAME)
    except Exception as exc:
        logger.warning("[db] SQLAlchemy setup failed — starting without persistence: %s", exc)

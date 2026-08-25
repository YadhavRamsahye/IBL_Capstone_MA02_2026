"""
seed_users.py
Create the initial administrator account.

Author : Mokshan Mehess (22703417) — Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System

Changes from the previous version:
  * Credentials come from the environment, not a hardcoded literal — the
    database password was previously committed to source.
  * Passwords are prompted for rather than defined in the file. The old
    version shipped `admin/admin123` and `user/password` in plain text, and
    those accounts existed in every checkout of the repository.
  * Column names match the current schema (`password_hash`, not
    `"PasswordHash"`).

Usage:  python seed_users.py
"""

import asyncio
import getpass
import os
import sys
import uuid

import asyncpg
import bcrypt
from dotenv import load_dotenv

load_dotenv()

HOST     = os.getenv("DB_HOST", "localhost")
PORT     = int(os.getenv("DB_PORT", "5432"))
USER     = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME  = os.getenv("DB_NAME", "trafficsystem")

MIN_PASSWORD_LEN = 8


def prompt_credentials() -> tuple[str, str, str]:
    username = input("Admin username [admin]: ").strip() or "admin"
    email = input("Admin email [admin@example.com]: ").strip() or "admin@example.com"
    while "@" not in email or "." not in email.rpartition("@")[2]:
        print("  Enter a valid email address.")
        email = input("Admin email [admin@example.com]: ").strip() or "admin@example.com"
    while True:
        pw = getpass.getpass("Password: ")
        if len(pw) < MIN_PASSWORD_LEN:
            print(f"  Too short — minimum {MIN_PASSWORD_LEN} characters.")
            continue
        if pw != getpass.getpass("Confirm password: "):
            print("  Passwords do not match.")
            continue
        return username, email, pw


async def seed() -> None:
    if not PASSWORD:
        sys.exit(
            "DB_PASSWORD is not set. Add it to your .env file:\n"
            "    DB_PASSWORD=your-postgres-password"
        )

    username, email, password = prompt_credentials()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = await asyncpg.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB_NAME
    )
    try:
        existing = await conn.fetchval(
            "SELECT 1 FROM users WHERE username = $1", username
        )
        if existing:
            # Re-running should be able to reset a forgotten password, but only
            # when the operator explicitly says so.
            if input(f"User '{username}' exists. Reset its password? [y/N]: ").lower() != "y":
                print("Left unchanged.")
                return
            await conn.execute(
                "UPDATE users SET password_hash = $1 WHERE username = $2",
                hashed, username,
            )
            print(f"Password reset for '{username}'.")
            return

        await conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role) "
            "VALUES ($1, $2, $3, $4, $5)",
            str(uuid.uuid4()), username, email, hashed, "admin",
        )
        print(f"Created admin user '{username}'.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())

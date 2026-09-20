"""
seed_users.py
Create the initial administrator account.

Author : Mokshan Mehess (22703417) - Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group - Traffic Bottleneck Detection System

Changes from the previous version:
  * Credentials come from the environment, not a hardcoded literal - the
    database password was previously committed to source.
  * Passwords are prompted for rather than defined in the file. The old
    version shipped `admin/admin123` and `user/password` in plain text, and
    those accounts existed in every checkout of the repository.
  * Column names match the current schema (`password_hash`, not
    `"PasswordHash"`).
  * An email address is prompted for and stored. Accounts created without
    one cannot use password recovery at all: the reset form finds the user,
    sees no address, and deliberately says nothing.

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

import mailer

load_dotenv()

HOST     = os.getenv("DB_HOST", "localhost")
PORT     = int(os.getenv("DB_PORT", "5432"))
USER     = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME  = os.getenv("DB_NAME", "trafficsystem")

MIN_PASSWORD_LEN = 8


def prompt_email() -> str:
    """Recovery address. Optional, so an operator who has not decided yet is
    not blocked - but the consequence is stated rather than left to be
    discovered at reset time."""
    while True:
        email = input("Email for password recovery (blank to skip): ").strip()
        if not email:
            print("  No address given - an account without one cannot use "
                  "email recovery.")
            return ""
        if mailer.is_valid_email(email):
            return email
        print("  That does not look like an email address.")


def prompt_credentials() -> tuple[str, str, str]:
    username = input("Admin username [admin]: ").strip() or "admin"
    email = prompt_email()
    while True:
        pw = getpass.getpass("Password: ")
        if len(pw) < MIN_PASSWORD_LEN:
            print(f"  Too short - minimum {MIN_PASSWORD_LEN} characters.")
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
            if email:
                await conn.execute(
                    "UPDATE users SET password_hash = $1, email = $2 "
                    "WHERE username = $3",
                    hashed, email, username,
                )
                print(f"Password reset for '{username}', email set to {email}.")
            else:
                await conn.execute(
                    "UPDATE users SET password_hash = $1 WHERE username = $2",
                    hashed, username,
                )
                print(f"Password reset for '{username}'. Email left unchanged.")
            return

        await conn.execute(
            "INSERT INTO users (id, username, password_hash, role, email) "
            "VALUES ($1, $2, $3, $4, $5)",
            str(uuid.uuid4()), username, hashed, "admin", email or None,
        )
        print(f"Created admin user '{username}'"
              + (f" with recovery address {email}." if email else "."))
    except asyncpg.UniqueViolationError:
        # Addresses are unique case-insensitively (idx_users_email_lower), so
        # name the account that already holds it rather than surfacing a raw
        # constraint violation.
        holder = await conn.fetchval(
            "SELECT username FROM users WHERE LOWER(email) = LOWER($1)", email)
        sys.exit(f"'{email}' is already the recovery address for '{holder}'.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())

"""
run_schema.py
Create the TrafficSystem database and apply schema.sql to it.

Author : Mokshan Mehess (22703417) - Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group - Traffic Bottleneck Detection System

This script used to carry its own inline copy of the DDL, which had drifted from
schema.sql - different column names, different quoting - so the schema you ended
up with depended on which file you ran. schema.sql is now the only definition
and this script simply executes it.

Credentials come from the same environment variables as database.py so there is
one place to configure them:

    DB_HOST  DB_PORT  DB_USER  DB_PASSWORD  DB_NAME

Usage:  python run_schema.py
"""

import asyncio
import os
import pathlib
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

HOST     = os.getenv("DB_HOST", "localhost")
PORT     = int(os.getenv("DB_PORT", "5432"))
USER     = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME  = os.getenv("DB_NAME", "trafficsystem")

SCHEMA_FILE = pathlib.Path(__file__).with_name("schema.sql")


async def run() -> None:
    if not PASSWORD:
        sys.exit(
            "DB_PASSWORD is not set. Add it to your .env file:\n"
            "    DB_PASSWORD=your-postgres-password"
        )

    if not SCHEMA_FILE.exists():
        sys.exit(f"schema.sql not found next to {__file__}")

    # Step 1 - create the database if it does not exist. CREATE DATABASE cannot
    # run inside a transaction, so it needs its own connection to `postgres`.
    conn = await asyncpg.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, database="postgres"
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", DB_NAME
        )
        if exists:
            print(f"Database '{DB_NAME}' already exists.")
        else:
            # Identifier cannot be parameterised; DB_NAME is operator-supplied
            # config, not user input, but quote it defensively anyway.
            await conn.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"Database '{DB_NAME}' created.")
    finally:
        await conn.close()

    # Step 2 - apply the schema. Every statement is idempotent (IF NOT EXISTS /
    # duplicate_object guards), so re-running this is safe.
    conn = await asyncpg.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB_NAME
    )
    try:
        await conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        print(f"Applied {SCHEMA_FILE.name}.")

        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        print("\nTables now present:")
        for row in tables:
            print(f"  - {row['tablename']}")
    finally:
        await conn.close()

    print("\nSchema done. Next: python seed_users.py")


if __name__ == "__main__":
    asyncio.run(run())

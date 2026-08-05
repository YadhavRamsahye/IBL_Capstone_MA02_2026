"""
Author : Mokshan Mehess (22703417) — Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System
"""

# check_db.py
import asyncio
import asyncpg

HOST     = "localhost"
PORT     = 5432
USER     = "postgres"
PASSWORD = "tqu9vfds"   # ← your password
DB_NAME  = "TrafficSystem"

async def check():
    conn = await asyncpg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB_NAME)

    print("\n===== USERS =====")
    users = await conn.fetch('SELECT id, username, role, "LastLogin" FROM users')
    for u in users:
        print(dict(u))

    print("\n===== CAMERAS =====")
    cameras = await conn.fetch('SELECT id, name, latitude, longitude, "isActive" FROM cameras')
    for c in cameras:
        print(dict(c))

    print("\n===== ALERTS =====")
    alerts = await conn.fetch('SELECT * FROM alerts LIMIT 10')
    for a in alerts:
        print(dict(a))

    print("\n===== TABLES IN DATABASE =====")
    tables = await conn.fetch("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    for t in tables:
        print(" -", t["table_name"])

    await conn.close()

asyncio.run(check())
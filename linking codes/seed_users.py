# seed_users.py
import asyncio
import uuid
import bcrypt
import asyncpg

HOST     = "localhost"
PORT     = 5432
USER     = "postgres"
PASSWORD = "tqu9vfds"   # ← only change this
DB_NAME  = "TrafficSystem"

USERS = [
    {"username": "admin", "password": "admin123", "role": "admin"},
    {"username": "user",  "password": "password", "role": "user"},
]

async def seed():
    conn = await asyncpg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB_NAME)

    for u in USERS:
        hashed = bcrypt.hashpw(u["password"].encode(), bcrypt.gensalt()).decode()
        await conn.execute("""
            INSERT INTO users (id, username, "PasswordHash", role)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (username) DO NOTHING
        """, str(uuid.uuid4()), u["username"], hashed, u["role"])
        print(f"Inserted: {u['username']}")

    await conn.close()
    print("Seeding done.")

asyncio.run(seed())
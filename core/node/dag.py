"""
Agnet Protocol (AGN)
core/node/dag.py

DAG storage — PostgreSQL for production, SQLite for local dev.
Automatically switches based on DATABASE_URL environment variable.
"""

import os
import time
from typing import Optional, List

DATABASE_URL = os.environ.get("DATABASE_URL")
print(f"DAG using: {'postgresql' if DATABASE_URL else 'sqlite'} | URL set: {bool(DATABASE_URL)}", flush=True)

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    def get_conn():
        return psycopg2.connect(DATABASE_URL)
    PLACEHOLDER = "%s"
else:
    import sqlite3
    def get_conn():
        conn = sqlite3.connect("agnet.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    PLACEHOLDER = "?"


GENESIS_TX_ID = "0" * 64


class DAG:
    def __init__(self):
        self._init_schema()

    def _get_conn(self):
        return get_conn()

    def _init_schema(self):
        conn = self._get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    amount BIGINT NOT NULL,
                    timestamp BIGINT NOT NULL,
                    nonce BIGINT NOT NULL,
                    confirm_0 TEXT NOT NULL,
                    confirm_1 TEXT NOT NULL,
                    layer INTEGER NOT NULL,
                    memo TEXT,
                    signature TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at BIGINT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_sender ON transactions(sender)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_receiver ON transactions(receiver)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS balances (
                    address TEXT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0,
                    updated_at BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS nonces (
                    address TEXT NOT NULL,
                    nonce BIGINT NOT NULL,
                    PRIMARY KEY (address, nonce)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS confirmations (
                    tx_id TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    PRIMARY KEY (tx_id, confirmed_by)
                )
            """)
        else:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY, sender TEXT NOT NULL, receiver TEXT NOT NULL,
                    amount INTEGER NOT NULL, timestamp INTEGER NOT NULL, nonce INTEGER NOT NULL,
                    confirm_0 TEXT NOT NULL, confirm_1 TEXT NOT NULL, layer INTEGER NOT NULL,
                    memo TEXT, signature TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tx_sender ON transactions(sender);
                CREATE TABLE IF NOT EXISTS balances (address TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS nonces (address TEXT NOT NULL, nonce INTEGER NOT NULL, PRIMARY KEY (address, nonce));
                CREATE TABLE IF NOT EXISTS confirmations (tx_id TEXT NOT NULL, confirmed_by TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (tx_id, confirmed_by));
            """)
        conn.commit()
        conn.close()

    def tx_exists(self, tx_id: str) -> bool:
        if tx_id == GENESIS_TX_ID:
            return True
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM transactions WHERE id = {PLACEHOLDER}", (tx_id,))
        row = cur.fetchone()
        conn.close()
        return row is not None

    def get_sender_of(self, tx_id: str) -> Optional[str]:
        if tx_id == GENESIS_TX_ID:
            return None
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT sender FROM transactions WHERE id = {PLACEHOLDER}", (tx_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0] if DATABASE_URL else row["sender"]
        return None

    def insert_tx(self, tx) -> bool:
        if self.tx_exists(tx.id):
            return False
        now = int(time.time() * 1000)
        conn = self._get_conn()
        cur = conn.cursor()
        p = PLACEHOLDER
        cur.execute(f"""
            INSERT INTO transactions (id, sender, receiver, amount, timestamp, nonce,
                confirm_0, confirm_1, layer, memo, signature, version, created_at)
            VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
            ON CONFLICT(id) DO NOTHING
        """, (tx.id, tx.sender, tx.receiver, tx.amount, tx.timestamp, tx.nonce,
              tx.confirms[0], tx.confirms[1], tx.layer, tx.memo, tx.signature, tx.version, now))
        if tx.amount > 0:
            self._update_balance(cur, tx.sender, -tx.amount, now)
            self._update_balance(cur, tx.receiver, tx.amount, now)
        cur.execute(f"INSERT INTO nonces (address, nonce) VALUES ({p},{p}) ON CONFLICT DO NOTHING", (tx.sender, tx.nonce))
        for cid in tx.confirms:
            if cid != GENESIS_TX_ID:
                cur.execute(f"INSERT INTO confirmations (tx_id, confirmed_by, created_at) VALUES ({p},{p},{p}) ON CONFLICT DO NOTHING", (cid, tx.id, now))
        conn.commit()
        conn.close()
        return True

    def get_balance(self, address: str) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT balance FROM balances WHERE address = {PLACEHOLDER}", (address,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0] if DATABASE_URL else row["balance"]
        return 0

    def credit(self, address: str, amount_nagn: int) -> None:
        now = int(time.time() * 1000)
        conn = self._get_conn()
        cur = conn.cursor()
        self._update_balance(cur, address, amount_nagn, now)
        conn.commit()
        conn.close()

    def _update_balance(self, cur, address: str, delta: int, now: int) -> None:
        p = PLACEHOLDER
        if DATABASE_URL:
            cur.execute(f"""
                INSERT INTO balances (address, balance, updated_at) VALUES ({p}, GREATEST(0,{p}), {p})
                ON CONFLICT(address) DO UPDATE SET
                    balance = GREATEST(0, balances.balance + {p}),
                    updated_at = {p}
            """, (address, delta, now, delta, now))
        else:
            cur.execute(f"""
                INSERT INTO balances (address, balance, updated_at) VALUES ({p}, MAX(0,{p}), {p})
                ON CONFLICT(address) DO UPDATE SET
                    balance = MAX(0, balance + {p}), updated_at = {p}
            """, (address, delta, now, delta, now))

    def nonce_used(self, address: str, nonce: int) -> bool:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM nonces WHERE address = {PLACEHOLDER} AND nonce = {PLACEHOLDER}", (address, nonce))
        row = cur.fetchone()
        conn.close()
        return row is not None

    def next_nonce(self, address: str) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT MAX(nonce) FROM nonces WHERE address = {PLACEHOLDER}", (address,))
        row = cur.fetchone()
        conn.close()
        val = row[0] if row and row[0] is not None else -1
        return val + 1

    def get_tips(self, exclude_sender: str = None) -> List[str]:
        conn = self._get_conn()
        cur = conn.cursor()
        p = PLACEHOLDER
        if exclude_sender:
            cur.execute(f"""
                SELECT t.id FROM transactions t
                LEFT JOIN confirmations c ON t.id = c.tx_id
                WHERE c.tx_id IS NULL AND t.sender != {p}
                ORDER BY t.timestamp ASC
                LIMIT 100
            """, (exclude_sender,))
        else:
            cur.execute("""
                SELECT t.id FROM transactions t
                LEFT JOIN confirmations c ON t.id = c.tx_id
                WHERE c.tx_id IS NULL
                ORDER BY t.timestamp ASC
                LIMIT 100
            """)
        rows = cur.fetchall()
        conn.close()
        tips = [r[0] if DATABASE_URL else r["id"] for r in rows]
        while len(tips) < 2:
            tips.append(GENESIS_TX_ID)
        return tips[:2]

    def stats(self) -> dict:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transactions")
        tx_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM balances WHERE balance > 0")
        addr_count = cur.fetchone()[0]
        conn.close()
        return {"tx_count": tx_count, "active_addresses": addr_count, "tips": self.get_tips()}

    def close(self):
        pass

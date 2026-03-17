"""
Agnet Protocol (AGN)
core/contracts/staking.py

AGP-1-SC — Staking Contract.
PostgreSQL + SQLite auto-switch via DATABASE_URL.
"""

import os
import math
import time

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    def get_conn():
        return psycopg2.connect(DATABASE_URL)
    P = "%s"
else:
    import sqlite3
    def get_conn():
        conn = sqlite3.connect("agnet.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    P = "?"

MIN_STAKE_AGENT = 10_000_000
MIN_STAKE_HUMAN = 100_000_000
LOCK_PERIOD_SECONDS = 7 * 24 * 3600
GENESIS_THRESHOLD = 100
ROTATION_FREEZE_SECONDS = 30


class ParticipantType:
    AGENT = 1
    HUMAN = 2

    def __init__(self, value):
        self.value = value

    @property
    def name(self):
        return "agent" if self.value == 1 else "human"


class StakingContract:
    def __init__(self, db=None):
        self._init_schema()

    def _init_schema(self):
        conn = get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute("""CREATE TABLE IF NOT EXISTS stakes (
                address TEXT PRIMARY KEY, participant_type INTEGER NOT NULL,
                amount BIGINT NOT NULL, locked_until BIGINT NOT NULL,
                registered_at BIGINT NOT NULL, genesis_weight INTEGER NOT NULL DEFAULT 0)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS rotations (
                old_address TEXT PRIMARY KEY, new_public_key TEXT NOT NULL,
                initiated_at BIGINT NOT NULL, completed INTEGER NOT NULL DEFAULT 0)""")
        else:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS stakes (
                    address TEXT PRIMARY KEY, participant_type INTEGER NOT NULL,
                    amount INTEGER NOT NULL, locked_until INTEGER NOT NULL,
                    registered_at INTEGER NOT NULL, genesis_weight INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS rotations (
                    old_address TEXT PRIMARY KEY, new_public_key TEXT NOT NULL,
                    initiated_at INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
            """)
        conn.commit()
        conn.close()

    def min_stake_for(self, participant_type):
        val = participant_type.value if hasattr(participant_type, 'value') else int(participant_type)
        return MIN_STAKE_AGENT if val == 1 else MIN_STAKE_HUMAN

    def stake(self, address, amount_nagn, participant_type, genesis_weight=0):
        if amount_nagn < self.min_stake_for(participant_type):
            return False
        now = int(time.time())
        locked_until = now + LOCK_PERIOD_SECONDS
        ptype = participant_type.value if hasattr(participant_type, 'value') else int(participant_type)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"""INSERT INTO stakes (address, participant_type, amount, locked_until, registered_at, genesis_weight)
            VALUES ({P},{P},{P},{P},{P},{P})
            ON CONFLICT(address) DO UPDATE SET amount={P}, locked_until={P}""",
            (address, ptype, amount_nagn, locked_until, now, genesis_weight, amount_nagn, locked_until))
        conn.commit()
        conn.close()
        return True

    def unstake(self, address):
        now = int(time.time())
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT locked_until FROM stakes WHERE address={P}", (address,))
        row = cur.fetchone()
        if not row or (row[0] if DATABASE_URL else row["locked_until"]) > now:
            conn.close()
            return False
        cur.execute(f"DELETE FROM stakes WHERE address={P}", (address,))
        conn.commit()
        conn.close()
        return True

    def weight(self, address):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM stakes WHERE address={P}", (address,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return 0.0
        if DATABASE_URL:
            amount, ptype, reg_at, gw = row[2], row[1], row[4], row[5]
        else:
            amount, ptype, reg_at, gw = row["amount"], row["participant_type"], row["registered_at"], row["genesis_weight"]
        min_s = MIN_STAKE_AGENT if ptype == 1 else MIN_STAKE_HUMAN
        if amount < min_s:
            return 0.0
        if gw > 0 and not self._genesis_exited():
            return float(gw)
        days = (int(time.time()) - reg_at) / 86400
        return math.log(days + 1) * (amount / min_s)

    def weight_with_tx_count(self, address, tx_count):
        partial = self.weight(address)
        if partial == 0.0:
            return 0.0
        return partial * math.log(tx_count + 1)

    def total_weight(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT address FROM stakes")
        rows = cur.fetchall()
        conn.close()
        return sum(self.weight(r[0] if DATABASE_URL else r["address"]) for r in rows)

    def _genesis_exited(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT SUM(genesis_weight) FROM stakes")
        row = cur.fetchone()
        conn.close()
        total = row[0] or 0
        return total >= GENESIS_THRESHOLD

    def is_registered(self, address):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM stakes WHERE address={P}", (address,))
        row = cur.fetchone()
        conn.close()
        return row is not None

    def get_stake(self, address):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT amount FROM stakes WHERE address={P}", (address,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0] if DATABASE_URL else row["amount"]
        return None

    def initiate_rotation(self, old_address, new_public_key):
        if not self.is_registered(old_address):
            return False
        now = int(time.time())
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"""INSERT INTO rotations (old_address, new_public_key, initiated_at)
            VALUES ({P},{P},{P}) ON CONFLICT(old_address) DO UPDATE SET
            new_public_key={P}, initiated_at={P}, completed=0""",
            (old_address, new_public_key, now, new_public_key, now))
        conn.commit()
        conn.close()
        return True

    def is_frozen(self, address):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT initiated_at FROM rotations WHERE old_address={P} AND completed=0", (address,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return False
        initiated = row[0] if DATABASE_URL else row["initiated_at"]
        return int(time.time()) < initiated + ROTATION_FREEZE_SECONDS

    def complete_rotation(self, old_address):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"UPDATE rotations SET completed=1 WHERE old_address={P}", (old_address,))
        cur.execute(f"DELETE FROM stakes WHERE address={P}", (old_address,))
        conn.commit()
        conn.close()
        return True

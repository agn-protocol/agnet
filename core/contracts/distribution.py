"""
Agnet Protocol (AGN)
core/contracts/distribution.py

AGP-1-DC — Distribution Contract.
PostgreSQL + SQLite auto-switch via DATABASE_URL.
"""

import os
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

TOTAL_SUPPLY_NAGN = 1_000_000_000 * 1_000_000
BASE_REWARD_NAGN = 50 * 1_000_000
EPOCH_DURATION_SECONDS = 24 * 3600
HALVING_INTERVAL_EPOCHS = 365 * 4
GENESIS_REWARD_NAGN = 100 * 1_000_000
GENESIS_MAX_NODES = 100

# ── Governance parameter: minimum base emission ────────────────────────────────
# After main supply (1B AGN) is fully distributed through halving cycles,
# the network continues emitting this amount per epoch to permanently
# incentivize validators. Default: 1 AGN/epoch (~365 AGN/year total).
# Can be updated via network governance vote stored in distribution_state.
DEFAULT_MIN_BASE_EMISSION_NAGN = 1 * 1_000_000  # 1 AGN per epoch


class DistributionContract:
    def __init__(self, db=None):
        self._init_schema()

    def _init_schema(self):
        conn = get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute("""CREATE TABLE IF NOT EXISTS distribution_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS epoch_rewards (
                epoch BIGINT PRIMARY KEY, reward BIGINT NOT NULL,
                distributed INTEGER NOT NULL DEFAULT 0,
                epoch_start BIGINT NOT NULL, epoch_end BIGINT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS genesis_nodes (
                address TEXT PRIMARY KEY, rewarded_at BIGINT NOT NULL)""")
        else:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS distribution_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS epoch_rewards (epoch INTEGER PRIMARY KEY, reward INTEGER NOT NULL, distributed INTEGER NOT NULL DEFAULT 0, epoch_start INTEGER NOT NULL, epoch_end INTEGER);
                CREATE TABLE IF NOT EXISTS genesis_nodes (address TEXT PRIMARY KEY, rewarded_at INTEGER NOT NULL);
            """)
        conn.commit()
        self._ensure_state(conn)
        conn.close()

    def _ensure_state(self, conn):
        cur = conn.cursor()
        for key, val in [
            ("total_distributed", "0"),
            ("genesis_count", "0"),
            ("launch_time", str(int(time.time()))),
            ("min_base_emission_nagn", str(DEFAULT_MIN_BASE_EMISSION_NAGN)),
        ]:
            cur.execute(f"INSERT INTO distribution_state (key, value) VALUES ({P},{P}) ON CONFLICT(key) DO NOTHING", (key, val))
        conn.commit()

    def _get_state(self, key):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT value FROM distribution_state WHERE key={P}", (key,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else "0"

    def _set_state(self, key, value):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"INSERT INTO distribution_state (key,value) VALUES ({P},{P}) ON CONFLICT(key) DO UPDATE SET value={P}", (key, value, value))
        conn.commit()
        conn.close()

    def genesis_reward(self, address):
        genesis_count = int(self._get_state("genesis_count"))
        if genesis_count >= GENESIS_MAX_NODES:
            return None
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM genesis_nodes WHERE address={P}", (address,))
        if cur.fetchone():
            conn.close()
            return None
        total = int(self._get_state("total_distributed"))
        if total + GENESIS_REWARD_NAGN > TOTAL_SUPPLY_NAGN:
            conn.close()
            return None
        now = int(time.time())
        cur.execute(f"INSERT INTO genesis_nodes (address, rewarded_at) VALUES ({P},{P}) ON CONFLICT DO NOTHING", (address, now))
        conn.commit()
        conn.close()
        self._set_state("genesis_count", str(genesis_count + 1))
        self._set_state("total_distributed", str(total + GENESIS_REWARD_NAGN))
        return GENESIS_REWARD_NAGN

    def genesis_open(self):
        return int(self._get_state("genesis_count")) < GENESIS_MAX_NODES

    def genesis_count(self):
        return int(self._get_state("genesis_count"))

    def current_epoch(self):
        launch_time = int(self._get_state("launch_time"))
        return (int(time.time()) - launch_time) // EPOCH_DURATION_SECONDS

    def min_base_emission(self) -> int:
        """Current minimum base emission per epoch (governance parameter)."""
        return int(self._get_state("min_base_emission_nagn") or DEFAULT_MIN_BASE_EMISSION_NAGN)

    def set_min_base_emission(self, nagn: int) -> None:
        """Update minimum base emission. Called by governance vote."""
        self._set_state("min_base_emission_nagn", str(max(0, nagn)))

    def epoch_reward(self, epoch):
        halvings = epoch // HALVING_INTERVAL_EPOCHS
        halving_reward = BASE_REWARD_NAGN >> halvings
        # Floor is governance parameter, not zero — keeps validators incentivised forever
        return max(halving_reward, self.min_base_emission())

    def distribute_epoch(self, epoch, validator_stats):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT distributed FROM epoch_rewards WHERE epoch={P}", (epoch,))
        row = cur.fetchone()
        conn.close()
        if row and (row[0] if DATABASE_URL else row["distributed"]):
            return {}
        if not validator_stats:
            return {}
        reward = self.epoch_reward(epoch)
        total = int(self._get_state("total_distributed"))
        min_emission = self.min_base_emission()
        # Main supply cap applies only to halving rewards.
        # Base emission (floor) continues beyond 1B supply — this is the
        # perpetual network sustainability mechanism.
        halving_reward = max(BASE_REWARD_NAGN >> (epoch // HALVING_INTERVAL_EPOCHS), 0)
        if halving_reward > 0 and total + halving_reward > TOTAL_SUPPLY_NAGN:
            # Main supply nearly exhausted — switch to base emission only
            reward = min_emission
        if reward <= 0:
            return {}
        total_tx = sum(validator_stats.values())
        if total_tx == 0:
            return {}
        distributions = {}
        remaining = reward
        items = sorted(validator_stats.items(), key=lambda x: x[1], reverse=True)
        for i, (addr, tx_count) in enumerate(items):
            share = remaining if i == len(items) - 1 else int(reward * tx_count / total_tx)
            if share > 0:
                distributions[addr] = share
                remaining -= share
        now = int(time.time())
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO epoch_rewards (epoch,reward,distributed,epoch_start,epoch_end) VALUES ({P},{P},1,{P},{P}) ON CONFLICT(epoch) DO UPDATE SET distributed=1,epoch_end={P}",
            (epoch, reward, now - EPOCH_DURATION_SECONDS, now, now))
        conn.commit()
        conn.close()
        self._set_state("total_distributed", str(total + sum(distributions.values())))
        return distributions

    def total_distributed(self):
        return int(self._get_state("total_distributed"))

    def remaining_supply(self):
        return TOTAL_SUPPLY_NAGN - self.total_distributed()

    def stats(self):
        epoch = self.current_epoch()
        total = self.total_distributed()
        return {
            "current_epoch": epoch,
            "epoch_reward_nagn": self.epoch_reward(epoch),
            "total_distributed_nagn": total,
            "remaining_supply_nagn": self.remaining_supply(),
            "genesis_open": self.genesis_open(),
            "genesis_count": self.genesis_count(),
            "genesis_max": GENESIS_MAX_NODES,
            "min_base_emission_nagn": self.min_base_emission(),
            "min_base_emission_agn": self.min_base_emission() / 1_000_000,
            "main_supply_exhausted": total >= TOTAL_SUPPLY_NAGN,
        }

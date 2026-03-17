"""
Agnet Protocol (AGN)
core/contracts/distribution.py

AGP-1-DC — Distribution Contract.
Manages AGN emission, node rewards, halving, and genesis.
Immutable after deployment — all nodes run identical logic.
"""

import time
import sqlite3
from typing import Dict, List, Optional


# Total supply — fixed forever
TOTAL_SUPPLY_NAGN = 1_000_000_000 * 1_000_000  # 1B AGN in nAGN

# Base reward per epoch (first epoch)
BASE_REWARD_NAGN = 50 * 1_000_000  # 50 AGN in nAGN

# One epoch = 24 hours
EPOCH_DURATION_SECONDS = 24 * 3600

# Halving every 4 years
HALVING_INTERVAL_EPOCHS = 365 * 4  # epochs (days)

# Genesis parameters
GENESIS_REWARD_NAGN = 100 * 1_000_000  # 100 AGN per genesis node
GENESIS_MAX_NODES = 100


class DistributionContract:
    """
    AGP-1-DC — Distribution Contract.

    All AGN comes from this contract.
    No manual distribution. No special pools.
    Work = reward.
    """

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._init_schema()

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS distribution_state (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS epoch_rewards (
                epoch       INTEGER PRIMARY KEY,
                reward      INTEGER NOT NULL,
                distributed INTEGER NOT NULL DEFAULT 0,
                epoch_start INTEGER NOT NULL,
                epoch_end   INTEGER
            );

            CREATE TABLE IF NOT EXISTS genesis_nodes (
                address     TEXT PRIMARY KEY,
                rewarded_at INTEGER NOT NULL
            );
        """)

        # Initialize state if not exists
        self._set_state("total_distributed", "0")
        self._set_state("genesis_count", "0")
        self._set_state("launch_time", str(int(time.time())))

        self.db.commit()

    # ─── Genesis ──────────────────────────────────────────────────────

    def genesis_reward(self, address: str) -> Optional[int]:
        """
        Issue genesis reward to one of the first GENESIS_MAX_NODES nodes.

        Args:
            address: node address connecting for the first time

        Returns:
            Amount issued in nAGN, or None if genesis is closed
        """
        genesis_count = int(self._get_state("genesis_count"))

        if genesis_count >= GENESIS_MAX_NODES:
            return None  # genesis closed

        # Check not already rewarded
        existing = self.db.execute(
            "SELECT 1 FROM genesis_nodes WHERE address = ?", (address,)
        ).fetchone()
        if existing:
            return None

        # Check total supply
        total_distributed = int(self._get_state("total_distributed"))
        if total_distributed + GENESIS_REWARD_NAGN > TOTAL_SUPPLY_NAGN:
            return None

        now = int(time.time())
        self.db.execute(
            "INSERT INTO genesis_nodes (address, rewarded_at) VALUES (?, ?)",
            (address, now)
        )
        self._set_state("genesis_count", str(genesis_count + 1))
        self._set_state(
            "total_distributed",
            str(total_distributed + GENESIS_REWARD_NAGN)
        )
        self.db.commit()
        return GENESIS_REWARD_NAGN

    def genesis_open(self) -> bool:
        """Check if genesis is still accepting new nodes."""
        return int(self._get_state("genesis_count")) < GENESIS_MAX_NODES

    def genesis_count(self) -> int:
        """How many genesis nodes have been rewarded."""
        return int(self._get_state("genesis_count"))

    # ─── Epoch rewards ────────────────────────────────────────────────

    def current_epoch(self) -> int:
        """
        Calculate current epoch number based on launch time.
        One epoch = 24 hours.
        """
        launch_time = int(self._get_state("launch_time"))
        elapsed = int(time.time()) - launch_time
        return elapsed // EPOCH_DURATION_SECONDS

    def epoch_reward(self, epoch: int) -> int:
        """
        Calculate reward for a given epoch.

        Halving every HALVING_INTERVAL_EPOCHS epochs.
        BASE_REWARD >> halvings (right shift = divide by 2).
        """
        halvings = epoch // HALVING_INTERVAL_EPOCHS
        reward = BASE_REWARD_NAGN >> halvings
        return max(reward, 1)  # minimum 1 nAGN per epoch

    def distribute_epoch(
        self,
        epoch: int,
        validator_stats: Dict[str, int]
    ) -> Dict[str, int]:
        """
        Distribute rewards for a completed epoch.

        Args:
            epoch: epoch number
            validator_stats: {address: confirmed_tx_count}
                             addresses of validators and their TX counts

        Returns:
            {address: amount_nagn} — amounts distributed to each validator
        """
        # Check epoch not already distributed
        existing = self.db.execute(
            "SELECT distributed FROM epoch_rewards WHERE epoch = ?", (epoch,)
        ).fetchone()
        if existing and existing["distributed"]:
            return {}

        if not validator_stats:
            return {}

        reward = self.epoch_reward(epoch)
        total_distributed = int(self._get_state("total_distributed"))

        # Check supply cap
        if total_distributed + reward > TOTAL_SUPPLY_NAGN:
            reward = TOTAL_SUPPLY_NAGN - total_distributed
        if reward <= 0:
            return {}

        # Distribute proportionally by TX count
        total_tx = sum(validator_stats.values())
        if total_tx == 0:
            return {}

        distributions: Dict[str, int] = {}
        remaining = reward

        sorted_validators = sorted(
            validator_stats.items(),
            key=lambda x: x[1],
            reverse=True
        )

        for i, (address, tx_count) in enumerate(sorted_validators):
            if i == len(sorted_validators) - 1:
                # Last validator gets the remainder (avoid rounding loss)
                share = remaining
            else:
                share = int(reward * tx_count / total_tx)

            if share > 0:
                distributions[address] = share
                remaining -= share

        # Record epoch
        now = int(time.time())
        self.db.execute("""
            INSERT INTO epoch_rewards (epoch, reward, distributed, epoch_start, epoch_end)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(epoch) DO UPDATE SET distributed = 1, epoch_end = ?
        """, (epoch, reward, now - EPOCH_DURATION_SECONDS, now, now))

        # Update total distributed
        total_issued = sum(distributions.values())
        self._set_state(
            "total_distributed",
            str(total_distributed + total_issued)
        )
        self.db.commit()
        return distributions

    # ─── Stats ────────────────────────────────────────────────────────

    def total_distributed(self) -> int:
        """Total AGN distributed so far in nAGN."""
        return int(self._get_state("total_distributed"))

    def remaining_supply(self) -> int:
        """Remaining AGN to be distributed in nAGN."""
        return TOTAL_SUPPLY_NAGN - self.total_distributed()

    def stats(self) -> dict:
        """Distribution statistics."""
        epoch = self.current_epoch()
        return {
            "total_supply_nagn": TOTAL_SUPPLY_NAGN,
            "total_distributed_nagn": self.total_distributed(),
            "remaining_nagn": self.remaining_supply(),
            "current_epoch": epoch,
            "epoch_reward_nagn": self.epoch_reward(epoch),
            "genesis_open": self.genesis_open(),
            "genesis_count": self.genesis_count(),
        }

    # ─── Internal ─────────────────────────────────────────────────────

    def _get_state(self, key: str) -> str:
        row = self.db.execute(
            "SELECT value FROM distribution_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else "0"

    def _set_state(self, key: str, value: str) -> None:
        self.db.execute("""
            INSERT INTO distribution_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
        """, (key, value, value))

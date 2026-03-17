"""
Agnet Protocol (AGN)
core/contracts/staking.py

AGP-1-SC — Staking Contract.
Manages participant staking, weight calculation, and key rotation.
Logic is deterministic — all nodes compute identical results.
"""

import math
import time
import sqlite3
from typing import Optional
from enum import IntEnum


class ParticipantType(IntEnum):
    AGENT = 1
    HUMAN = 2


MIN_STAKE_AGENT = 10_000_000    # 10 AGN in nAGN
MIN_STAKE_HUMAN = 100_000_000   # 100 AGN in nAGN
LOCK_PERIOD_SECONDS = 7 * 24 * 3600   # 7 days
GENESIS_THRESHOLD = 100         # sum of weights to exit genesis mode
ROTATION_FREEZE_SECONDS = 30    # freeze window during key rotation


class StakingContract:
    """
    AGP-1-SC — Staking Contract.

    Immutable after deployment.
    All nodes run identical logic and reach the same state.
    """

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._init_schema()

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS stakes (
                address         TEXT PRIMARY KEY,
                participant_type INTEGER NOT NULL,
                amount          INTEGER NOT NULL,
                locked_until    INTEGER NOT NULL,
                registered_at   INTEGER NOT NULL,
                genesis_weight  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS rotations (
                old_address     TEXT PRIMARY KEY,
                new_public_key  TEXT NOT NULL,
                initiated_at    INTEGER NOT NULL,
                completed       INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.db.commit()

    def min_stake_for(self, participant_type: ParticipantType) -> int:
        """Return minimum stake in nAGN for participant type."""
        if participant_type == ParticipantType.AGENT:
            return MIN_STAKE_AGENT
        return MIN_STAKE_HUMAN

    def stake(
        self,
        address: str,
        amount_nagn: int,
        participant_type: ParticipantType,
        genesis_weight: int = 0
    ) -> bool:
        """
        Register a participant with a stake.

        Args:
            address: participant address
            amount_nagn: staked amount in nAGN
            participant_type: AGENT or HUMAN
            genesis_weight: 1 for genesis nodes, 0 for all others

        Returns:
            True if staked successfully
        """
        min_stake = self.min_stake_for(participant_type)
        if amount_nagn < min_stake:
            return False

        now = int(time.time())
        locked_until = now + LOCK_PERIOD_SECONDS

        self.db.execute("""
            INSERT INTO stakes
                (address, participant_type, amount, locked_until, registered_at, genesis_weight)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                amount = ?,
                locked_until = ?,
                genesis_weight = genesis_weight
        """, (
            address, int(participant_type), amount_nagn,
            locked_until, now, genesis_weight,
            amount_nagn, locked_until
        ))
        self.db.commit()
        return True

    def unstake(self, address: str) -> bool:
        """
        Remove stake if lock period has passed.

        Returns:
            True if unstaked successfully
        """
        now = int(time.time())
        row = self.db.execute(
            "SELECT locked_until, amount FROM stakes WHERE address = ?",
            (address,)
        ).fetchone()

        if not row:
            return False

        if now < row["locked_until"]:
            return False

        self.db.execute("DELETE FROM stakes WHERE address = ?", (address,))
        self.db.commit()
        return True

    def weight(self, address: str) -> float:
        """
        Calculate participant weight for consensus and reward distribution.

        Formula:
            W = log(T+1) * log(V+1) * (S / M)

        Where:
            T = days since registration
            V = confirmed TX count (passed in separately)
            S = staked amount in nAGN
            M = minimum stake for participant type

        Genesis nodes have baseline weight = 1 until genesis exits.
        """
        row = self.db.execute(
            "SELECT * FROM stakes WHERE address = ?", (address,)
        ).fetchone()

        if not row:
            return 0.0

        amount = row["amount"]
        participant_type = ParticipantType(row["participant_type"])
        min_stake = self.min_stake_for(participant_type)

        if amount < min_stake:
            return 0.0

        # Check if still in genesis mode
        if row["genesis_weight"] > 0 and not self._genesis_exited():
            return float(row["genesis_weight"])

        registered_at = row["registered_at"]
        now = int(time.time())
        days = (now - registered_at) / 86400

        # V is computed externally (confirmed TX count from DAG)
        # Here we return partial weight — caller multiplies by log(V+1)
        t_factor = math.log(days + 1)
        s_factor = amount / min_stake

        return t_factor * s_factor

    def weight_with_tx_count(self, address: str, confirmed_tx_count: int) -> float:
        """
        Full weight calculation including confirmed TX count.

        Args:
            address: participant address
            confirmed_tx_count: number of TXs confirmed by this participant
        """
        partial = self.weight(address)
        if partial == 0.0:
            return 0.0

        row = self.db.execute(
            "SELECT genesis_weight FROM stakes WHERE address = ?", (address,)
        ).fetchone()

        if row and row["genesis_weight"] > 0 and not self._genesis_exited():
            return float(row["genesis_weight"])

        v_factor = math.log(confirmed_tx_count + 1)
        return partial * v_factor

    def total_weight(self) -> float:
        """Sum of weights across all participants."""
        addresses = self.db.execute(
            "SELECT address FROM stakes"
        ).fetchall()
        return sum(self.weight(r["address"]) for r in addresses)

    def _genesis_exited(self) -> bool:
        """Check if network has exited genesis mode."""
        total = self.db.execute(
            "SELECT SUM(genesis_weight) as total FROM stakes"
        ).fetchone()
        if not total or not total["total"]:
            return True
        return total["total"] >= GENESIS_THRESHOLD

    def is_registered(self, address: str) -> bool:
        """Check if an address has an active stake."""
        row = self.db.execute(
            "SELECT 1 FROM stakes WHERE address = ?", (address,)
        ).fetchone()
        return row is not None

    def get_stake(self, address: str) -> Optional[int]:
        """Get staked amount in nAGN for an address."""
        row = self.db.execute(
            "SELECT amount FROM stakes WHERE address = ?", (address,)
        ).fetchone()
        return row["amount"] if row else None

    # ─── Key rotation ─────────────────────────────────────────────────

    def initiate_rotation(self, old_address: str, new_public_key: str) -> bool:
        """
        Initiate key rotation for a participant.

        Called when owner sends Command TX with rotate_key command.
        Freezes outgoing Value TXs from old_address for ROTATION_FREEZE_SECONDS.

        Args:
            old_address: address being rotated
            new_public_key: new public key hex

        Returns:
            True if rotation initiated
        """
        if not self.is_registered(old_address):
            return False

        now = int(time.time())
        self.db.execute("""
            INSERT INTO rotations (old_address, new_public_key, initiated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(old_address) DO UPDATE SET
                new_public_key = ?,
                initiated_at = ?,
                completed = 0
        """, (old_address, new_public_key, now, new_public_key, now))
        self.db.commit()
        return True

    def is_frozen(self, address: str) -> bool:
        """
        Check if outgoing TXs from address are frozen due to key rotation.
        """
        row = self.db.execute("""
            SELECT initiated_at FROM rotations
            WHERE old_address = ? AND completed = 0
        """, (address,)).fetchone()

        if not row:
            return False

        now = int(time.time())
        freeze_until = row["initiated_at"] + ROTATION_FREEZE_SECONDS
        return now < freeze_until

    def complete_rotation(self, old_address: str) -> bool:
        """
        Mark rotation as complete after balance has been transferred.
        Invalidates the old address.
        """
        self.db.execute("""
            UPDATE rotations SET completed = 1
            WHERE old_address = ?
        """, (old_address,))
        self.db.execute("DELETE FROM stakes WHERE address = ?", (old_address,))
        self.db.commit()
        return True

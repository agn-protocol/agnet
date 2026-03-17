"""
Agnet Protocol (AGN)
core/node/dag.py

DAG storage and account chain management.
SQLite-based persistent storage.
"""

import sqlite3
import json
import time
from typing import Optional, List, Dict
from pathlib import Path

from core.node.tx import Transaction, nagn_to_agn


GENESIS_TX_ID = "0000000000000000000000000000000000000000000000000000000000000000"
DB_PATH = "agnet.db"


class DAG:
    """
    Directed Acyclic Graph storage.

    Each participant has their own account chain.
    The global graph is all chains linked through confirmations.

    Storage: SQLite for simplicity and portability.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          TEXT PRIMARY KEY,
                sender      TEXT NOT NULL,
                receiver    TEXT NOT NULL,
                amount      INTEGER NOT NULL,
                timestamp   INTEGER NOT NULL,
                nonce       INTEGER NOT NULL,
                confirm_0   TEXT NOT NULL,
                confirm_1   TEXT NOT NULL,
                layer       INTEGER NOT NULL,
                memo        TEXT,
                signature   TEXT NOT NULL,
                version     INTEGER NOT NULL DEFAULT 1,
                finalized   INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tx_sender
                ON transactions(sender);

            CREATE INDEX IF NOT EXISTS idx_tx_receiver
                ON transactions(receiver);

            CREATE INDEX IF NOT EXISTS idx_tx_timestamp
                ON transactions(timestamp);

            CREATE TABLE IF NOT EXISTS balances (
                address     TEXT PRIMARY KEY,
                balance     INTEGER NOT NULL DEFAULT 0,
                updated_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nonces (
                address     TEXT NOT NULL,
                nonce       INTEGER NOT NULL,
                PRIMARY KEY (address, nonce)
            );

            CREATE TABLE IF NOT EXISTS confirmations (
                tx_id           TEXT NOT NULL,
                confirmed_by    TEXT NOT NULL,
                confirmer_weight REAL NOT NULL DEFAULT 0,
                created_at      INTEGER NOT NULL,
                PRIMARY KEY (tx_id, confirmed_by)
            );
        """)
        self.conn.commit()

    # ─── TX operations ───────────────────────────────────────────────

    def insert_tx(self, tx: Transaction) -> bool:
        """
        Insert a validated transaction into the DAG.

        Updates balances, records nonce, records confirmations.

        Returns:
            True if inserted, False if already exists
        """
        if self.tx_exists(tx.id):
            return False

        now = int(time.time() * 1000)

        self.conn.execute("""
            INSERT INTO transactions
                (id, sender, receiver, amount, timestamp, nonce,
                 confirm_0, confirm_1, layer, memo, signature, version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tx.id, tx.sender, tx.receiver, tx.amount,
            tx.timestamp, tx.nonce, tx.confirms[0], tx.confirms[1],
            tx.layer, tx.memo, tx.signature, tx.version, now
        ))

        # Update sender balance (subtract)
        if tx.amount > 0:
            self._update_balance(tx.sender, -tx.amount, now)
            self._update_balance(tx.receiver, tx.amount, now)

        # Record nonce
        self.conn.execute(
            "INSERT OR IGNORE INTO nonces (address, nonce) VALUES (?, ?)",
            (tx.sender, tx.nonce)
        )

        # Record that this TX confirms two others
        for confirmed_id in tx.confirms:
            if confirmed_id != GENESIS_TX_ID:
                self.conn.execute("""
                    INSERT OR IGNORE INTO confirmations
                        (tx_id, confirmed_by, created_at)
                    VALUES (?, ?, ?)
                """, (confirmed_id, tx.id, now))

        self.conn.commit()
        return True

    def tx_exists(self, tx_id: str) -> bool:
        """Check if a TX exists in the DAG."""
        if tx_id == GENESIS_TX_ID:
            return True
        row = self.conn.execute(
            "SELECT id FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        return row is not None

    def get_tx(self, tx_id: str) -> Optional[Transaction]:
        """Retrieve a TX by ID."""
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_tx(row)

    def get_sender_of(self, tx_id: str) -> Optional[str]:
        """Get the sender address of a TX."""
        if tx_id == GENESIS_TX_ID:
            return None
        row = self.conn.execute(
            "SELECT sender FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        return row["sender"] if row else None

    def get_recent_tx(self, limit: int = 10) -> List[Transaction]:
        """Get recent transactions ordered by timestamp."""
        rows = self.conn.execute(
            "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [self._row_to_tx(r) for r in rows]

    def get_tips(self) -> List[str]:
        """
        Get unconfirmed TX IDs (tips of the DAG).

        Tips are TXs that have not been confirmed by any other TX yet.
        New TXs should confirm two tips.
        """
        rows = self.conn.execute("""
            SELECT t.id FROM transactions t
            LEFT JOIN confirmations c ON t.id = c.tx_id
            WHERE c.tx_id IS NULL
            ORDER BY t.timestamp ASC
            LIMIT 100
        """).fetchall()

        tips = [r["id"] for r in rows]

        # Always return at least two tips
        # If DAG is empty — return two genesis placeholders
        while len(tips) < 2:
            tips.append(GENESIS_TX_ID)

        return tips[:2]

    def get_confirmation_count(self, tx_id: str) -> int:
        """Get number of confirmations for a TX."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM confirmations WHERE tx_id = ?",
            (tx_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    # ─── Balance operations ───────────────────────────────────────────

    def get_balance(self, address: str) -> int:
        """Get balance in nAGN for an address."""
        row = self.conn.execute(
            "SELECT balance FROM balances WHERE address = ?", (address,)
        ).fetchone()
        return row["balance"] if row else 0

    def credit(self, address: str, amount_nagn: int) -> None:
        """Add AGN to an address balance (used by distribution contract)."""
        now = int(time.time() * 1000)
        self._update_balance(address, amount_nagn, now)
        self.conn.commit()

    def _update_balance(self, address: str, delta: int, now: int) -> None:
        """Update balance by delta (can be negative for deductions)."""
        self.conn.execute("""
            INSERT INTO balances (address, balance, updated_at)
            VALUES (?, MAX(0, ?), ?)
            ON CONFLICT(address) DO UPDATE SET
                balance = MAX(0, balance + ?),
                updated_at = ?
        """, (address, delta, now, delta, now))

    # ─── Nonce operations ─────────────────────────────────────────────

    def nonce_used(self, address: str, nonce: int) -> bool:
        """Check if a nonce was already used by this address."""
        row = self.conn.execute(
            "SELECT 1 FROM nonces WHERE address = ? AND nonce = ?",
            (address, nonce)
        ).fetchone()
        return row is not None

    def next_nonce(self, address: str) -> int:
        """Get the next available nonce for an address."""
        row = self.conn.execute(
            "SELECT MAX(nonce) as max_nonce FROM nonces WHERE address = ?",
            (address,)
        ).fetchone()
        max_nonce = row["max_nonce"] if row and row["max_nonce"] is not None else -1
        return max_nonce + 1

    # ─── Stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return DAG statistics."""
        tx_count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM transactions"
        ).fetchone()["cnt"]

        address_count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM balances WHERE balance > 0"
        ).fetchone()["cnt"]

        tips = self.get_tips()

        return {
            "tx_count": tx_count,
            "active_addresses": address_count,
            "tips": tips,
        }

    # ─── Helpers ──────────────────────────────────────────────────────

    def _row_to_tx(self, row) -> Transaction:
        """Convert a DB row to a Transaction object."""
        from core.node.tx import Transaction
        return Transaction(
            version=row["version"],
            sender=row["sender"],
            receiver=row["receiver"],
            amount=row["amount"],
            timestamp=row["timestamp"],
            nonce=row["nonce"],
            confirms=(row["confirm_0"], row["confirm_1"]),
            layer=row["layer"],
            memo=row["memo"],
            signature=row["signature"],
            id=row["id"],
        )

    def close(self):
        """Close database connection."""
        self.conn.close()

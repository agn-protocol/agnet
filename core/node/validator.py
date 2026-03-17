"""
Agnet Protocol (AGN)
core/node/validator.py

Transaction validation according to AGP-1 rules.
All 9 conditions must pass for a TX to be accepted.
"""

import time
from dataclasses import dataclass
from typing import Optional, Protocol, Set

from core.node.tx import Transaction, TxType


TIMESTAMP_PAST_WINDOW_MS = 60_000   # 60 seconds back
TIMESTAMP_FUTURE_WINDOW_MS = 5_000  # 5 seconds forward

VALID_COMMANDS = {"rotate_key"}


class DAGStore(Protocol):
    """Interface for DAG storage — used by validator."""

    def tx_exists(self, tx_id: str) -> bool:
        """Check if a TX exists in the DAG."""
        ...

    def get_balance(self, address: str) -> int:
        """Get balance in nAGN for an address."""
        ...

    def get_sender_of(self, tx_id: str) -> Optional[str]:
        """Get the sender address of a TX."""
        ...

    def nonce_used(self, address: str, nonce: int) -> bool:
        """Check if a nonce was already used by this address."""
        ...


@dataclass
class ValidationResult:
    """Result of TX validation."""
    valid: bool
    error: Optional[str] = None

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True)

    @classmethod
    def fail(cls, error: str) -> "ValidationResult":
        return cls(valid=False, error=error)

    def __bool__(self):
        return self.valid


class Validator:
    """
    AGP-1 transaction validator.

    Checks all 9 rules in order.
    Fails fast on first violation.
    """

    def __init__(self, dag: DAGStore):
        self.dag = dag

    def validate(self, tx: Transaction) -> ValidationResult:
        """
        Validate a transaction against all AGP-1 rules.

        Rules:
            1. version == 1
            2. signature valid against sender public key
            3. balance check (Value TX) or memo check (Command TX)
            4. timestamp within allowed window
            5. both confirmed TXs exist in DAG
            6. confirmed TXs are different
            7. sender is not author of confirmed TXs
            8. nonce not previously used by this sender
            9. memo length <= 64 bytes
        """

        # Rule 1: version
        if tx.version != 1:
            return ValidationResult.fail(
                f"Invalid version: {tx.version}, expected 1"
            )

        # Rule 2: signature
        if not tx.signature:
            return ValidationResult.fail("Missing signature")

        if not tx.verify():
            return ValidationResult.fail("Invalid signature")

        # Rule 3: balance or command check
        if tx.tx_type == TxType.VALUE:
            from core.crypto.keys import public_key_to_address, decode_key
            sender_address = public_key_to_address(decode_key(tx.sender))
            balance = self.dag.get_balance(sender_address)
            if balance < tx.amount:
                return ValidationResult.fail(
                    f"Insufficient balance: have {balance} nAGN, need {tx.amount} nAGN"
                )
        else:
            # Command TX — memo must be present and valid
            if not tx.memo:
                return ValidationResult.fail(
                    "Command TX requires non-empty memo"
                )
            if not self._is_valid_command(tx.memo):
                return ValidationResult.fail(
                    f"Unknown command: {tx.memo}"
                )

        # Rule 4: timestamp
        now_ms = int(time.time() * 1000)
        earliest = now_ms - TIMESTAMP_PAST_WINDOW_MS
        latest = now_ms + TIMESTAMP_FUTURE_WINDOW_MS

        if not (earliest <= tx.timestamp <= latest):
            return ValidationResult.fail(
                f"Timestamp out of range: {tx.timestamp}, "
                f"allowed [{earliest}, {latest}]"
            )

        # Rule 5: confirmed TXs exist
        conf_0, conf_1 = tx.confirms

        if not self.dag.tx_exists(conf_0):
            return ValidationResult.fail(
                f"Confirmed TX not found: {conf_0}"
            )

        if not self.dag.tx_exists(conf_1):
            return ValidationResult.fail(
                f"Confirmed TX not found: {conf_1}"
            )

        # Rule 6: confirmed TXs are different
        # Exception: genesis TX ID is allowed to appear twice (cold start)
        GENESIS = "0" * 64
        if conf_0 == conf_1 and conf_0 != GENESIS:
            return ValidationResult.fail(
                "Confirmed TXs must be different"
            )

        # Rule 7: sender did not author the confirmed TXs
        sender_of_0 = self.dag.get_sender_of(conf_0)
        sender_of_1 = self.dag.get_sender_of(conf_1)

        if sender_of_0 == tx.sender:
            return ValidationResult.fail(
                f"Sender cannot confirm own TX: {conf_0}"
            )

        if sender_of_1 == tx.sender:
            return ValidationResult.fail(
                f"Sender cannot confirm own TX: {conf_1}"
            )

        # Rule 8: nonce not reused
        if self.dag.nonce_used(tx.sender, tx.nonce):
            return ValidationResult.fail(
                f"Nonce already used: {tx.nonce}"
            )

        # Rule 9: memo length
        if tx.memo and len(tx.memo.encode()) > 64:
            return ValidationResult.fail(
                f"Memo exceeds 64 bytes: {len(tx.memo.encode())} bytes"
            )

        return ValidationResult.ok()

    def _is_valid_command(self, memo: str) -> bool:
        """Check if memo contains a known command."""
        for cmd in VALID_COMMANDS:
            if memo.startswith(f"{cmd}:"):
                return True
        return False

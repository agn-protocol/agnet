"""
Agnet Protocol (AGN)
core/node/tx.py

AGP-1 transaction structure.
Serialization, deserialization, hashing.
"""

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from typing import Optional, Tuple
from enum import IntEnum

from core.crypto.keys import sign_message, verify_signature, decode_key, encode_key


class Layer(IntEnum):
    AGENT = 1
    HUMAN = 2


class TxType(IntEnum):
    VALUE = 1    # AGN transfer (amount > 0)
    COMMAND = 2  # control command (amount = 0, memo required)


NAGN_PER_AGN = 1_000_000  # 1 AGN = 1,000,000 nAGN


def agn_to_nagn(agn: float) -> int:
    """Convert AGN float to nAGN integer."""
    return int(agn * NAGN_PER_AGN)


def nagn_to_agn(nagn: int) -> float:
    """Convert nAGN integer to AGN float."""
    return nagn / NAGN_PER_AGN


@dataclass
class Transaction:
    """
    AGP-1 Transaction.

    Two types:
        Value TX   — amount > 0, transfers AGN
        Command TX — amount = 0, control command via memo
    """

    version: int                    # always 1
    sender: str                     # sender public key hex
    receiver: str                   # receiver public key hex
    amount: int                     # in nAGN, 0 for Command TX
    timestamp: int                  # unix milliseconds
    nonce: int                      # replay protection
    confirms: Tuple[str, str]       # hashes of two confirmed TXs
    layer: int                      # Layer.AGENT or Layer.HUMAN
    memo: Optional[str]             # context or command (max 64 bytes)
    signature: Optional[str] = None # Ed25519 signature hex
    id: Optional[str] = None        # TX hash, computed after signing

    @property
    def tx_type(self) -> TxType:
        return TxType.VALUE if self.amount > 0 else TxType.COMMAND

    def payload_bytes(self) -> bytes:
        """
        Canonical bytes for signing.
        Covers all fields except signature and id.
        """
        payload = {
            "version": self.version,
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "confirms": list(self.confirms),
            "layer": self.layer,
            "memo": self.memo or "",
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def compute_id(self) -> str:
        """Compute TX hash from payload bytes."""
        return hashlib.sha256(self.payload_bytes()).hexdigest()

    def sign(self, private_key: bytes) -> None:
        """Sign the transaction and set id and signature."""
        payload = self.payload_bytes()
        sig = sign_message(private_key, payload)
        self.signature = sig.hex()
        self.id = self.compute_id()

    def verify(self) -> bool:
        """Verify signature against sender public key."""
        if not self.signature:
            return False
        try:
            public_key = decode_key(self.sender)
            signature = decode_key(self.signature)
            return verify_signature(public_key, self.payload_bytes(), signature)
        except Exception:
            return False

    def to_dict(self) -> dict:
        """Serialize to dictionary for storage and transmission."""
        return {
            "version": self.version,
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "confirms": list(self.confirms),
            "layer": self.layer,
            "memo": self.memo,
            "signature": self.signature,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict) -> "Transaction":
        """Deserialize from dictionary."""
        return cls(
            version=data["version"],
            sender=data["sender"],
            receiver=data["receiver"],
            amount=data["amount"],
            timestamp=data["timestamp"],
            nonce=data["nonce"],
            confirms=tuple(data["confirms"]),
            layer=data["layer"],
            memo=data.get("memo"),
            signature=data.get("signature"),
            id=data.get("id"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Transaction":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def __repr__(self):
        return (
            f"TX(id={self.id[:8] if self.id else 'unsigned'}... "
            f"type={self.tx_type.name} "
            f"amount={nagn_to_agn(self.amount)} AGN)"
        )


def build_tx(
    sender_public_key: str,
    receiver: str,
    amount_agn: float,
    confirms: Tuple[str, str],
    layer: Layer,
    nonce: int,
    memo: Optional[str] = None,
) -> Transaction:
    """
    Build an unsigned transaction.

    Args:
        sender_public_key: sender public key hex
        receiver: receiver address
        amount_agn: amount in AGN (will be converted to nAGN)
        confirms: two TX hashes to confirm
        layer: Layer.AGENT or Layer.HUMAN
        nonce: unique nonce for this sender
        memo: optional context string (max 64 bytes)

    Returns:
        Unsigned Transaction ready to be signed
    """
    if memo and len(memo.encode()) > 64:
        raise ValueError(f"Memo exceeds 64 bytes: {len(memo.encode())} bytes")

    amount_nagn = agn_to_nagn(amount_agn)

    return Transaction(
        version=1,
        sender=sender_public_key,
        receiver=receiver,
        amount=amount_nagn,
        timestamp=int(time.time() * 1000),
        nonce=nonce,
        confirms=confirms,
        layer=int(layer),
        memo=memo,
    )


def build_command_tx(
    sender_public_key: str,
    receiver: str,
    command: str,
    confirms: Tuple[str, str],
    layer: Layer,
    nonce: int,
) -> Transaction:
    """
    Build a command transaction (amount=0).

    Commands:
        "rotate_key:{new_public_key_hex}"

    Args:
        command: command string (max 64 bytes)
    """
    return build_tx(
        sender_public_key=sender_public_key,
        receiver=receiver,
        amount_agn=0,
        confirms=confirms,
        layer=layer,
        nonce=nonce,
        memo=command,
    )

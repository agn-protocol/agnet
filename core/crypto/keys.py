"""
Agnet Protocol (AGN)
core/crypto/keys.py

Ed25519 key generation, address derivation, signing and verification.
Foundation of all cryptography in the protocol.
"""

import hashlib
import base64
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError
from typing import Tuple


ADDRESS_PREFIX = "agnet1"


def generate_keypair() -> Tuple[bytes, bytes]:
    """
    Generate a new Ed25519 key pair.

    Returns:
        (private_key, public_key) both as bytes

    Private key never leaves the device.
    Public key becomes the basis of the address.
    """
    signing_key = SigningKey.generate()
    private_key = bytes(signing_key)
    public_key = bytes(signing_key.verify_key)
    return private_key, public_key


def public_key_to_address(public_key: bytes) -> str:
    """
    Convert a public key to an Agnet address.

    Algorithm:
        1. SHA-256 of the public key
        2. Take first 20 bytes
        3. Base32 encode (lowercase, no padding)
        4. Prepend agnet1 prefix

    Args:
        public_key: Ed25519 public key (32 bytes)

    Returns:
        Address string like "agnet1a3f9c2e8b..."
    """
    digest = hashlib.sha256(public_key).digest()
    short = digest[:20]
    encoded = base64.b32encode(short).decode().lower().rstrip("=")
    return f"{ADDRESS_PREFIX}{encoded}"


def sign_message(private_key: bytes, message: bytes) -> bytes:
    """
    Sign a message with an Ed25519 private key.

    Args:
        private_key: private key (32 bytes)
        message: arbitrary bytes to sign

    Returns:
        Signature (64 bytes)
    """
    signing_key = SigningKey(private_key)
    signed = signing_key.sign(message)
    return signed.signature


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """
    Verify a signature against a public key.

    Args:
        public_key: public key (32 bytes)
        message: original message
        signature: signature to verify (64 bytes)

    Returns:
        True if valid, False otherwise
    """
    try:
        verify_key = VerifyKey(public_key)
        verify_key.verify(message, signature)
        return True
    except BadSignatureError:
        return False


def private_to_public(private_key: bytes) -> bytes:
    """Extract public key from private key."""
    signing_key = SigningKey(private_key)
    return bytes(signing_key.verify_key)


def encode_key(key: bytes) -> str:
    """Encode key as hex string for storage and transmission."""
    return key.hex()


def decode_key(key_hex: str) -> bytes:
    """Decode key from hex string."""
    return bytes.fromhex(key_hex)


class KeyPair:
    """
    Convenient wrapper around a key pair.
    Keeps private key in memory only.
    """

    def __init__(self, private_key: bytes):
        self.private_key = private_key
        self.public_key = private_to_public(private_key)
        self.address = public_key_to_address(self.public_key)

    @classmethod
    def generate(cls) -> "KeyPair":
        """Generate a new key pair."""
        private_key, _ = generate_keypair()
        return cls(private_key)

    @classmethod
    def from_hex(cls, private_key_hex: str) -> "KeyPair":
        """Restore key pair from hex private key."""
        return cls(decode_key(private_key_hex))

    def sign(self, message: bytes) -> bytes:
        """Sign a message."""
        return sign_message(self.private_key, message)

    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify a signature."""
        return verify_signature(self.public_key, message, signature)

    @property
    def private_hex(self) -> str:
        """Private key as hex — for saving to disk."""
        return encode_key(self.private_key)

    @property
    def public_hex(self) -> str:
        """Public key as hex — for transmission over the network."""
        return encode_key(self.public_key)

    def __repr__(self):
        return f"KeyPair(address={self.address})"

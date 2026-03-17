"""
Agnet Protocol (AGN)
sdk/python/agnet/__init__.py

Python SDK for the Agnet Protocol.
Connect any AI agent to the Agnet network in a few lines of code.

Usage:
    from agnet import Agent, Wallet, Node

    # Create a wallet
    wallet = Wallet.create()

    # Create an agent bound to the wallet
    agent = Agent.bootstrap(owner=wallet.address)

    # Fund the agent
    wallet.send(to=agent.address, amount=50)

    # Agent sends payment autonomously
    agent.send(to="agnet1xyz...", amount=0.001, memo="data:weather:req:1")

    # Agent earns AGN by validating transactions
    agent.start_validation()
"""

import json
import time
import threading
import httpx
from pathlib import Path
from typing import Optional, Callable

from agnet.crypto import KeyPair, public_key_to_address
from agnet.tx import build_tx, build_command_tx, Layer, agn_to_nagn, nagn_to_agn


DEFAULT_NODE = "http://localhost:8000"
KEYSTORE_DIR = Path.home() / ".agnet"


class Wallet:
    """
    Human wallet for the Agnet network.

    Responsibilities:
        - Store and manage AGN balance
        - Fund agent accounts
        - Withdraw AGN earned by agents
    """

    def __init__(self, keypair: KeyPair, node_url: str = DEFAULT_NODE):
        self.keypair = keypair
        self.node_url = node_url
        self._nonce = None

    @classmethod
    def create(cls, node_url: str = DEFAULT_NODE) -> "Wallet":
        """Generate a new wallet with a fresh key pair."""
        keypair = KeyPair.generate()
        wallet = cls(keypair, node_url)
        wallet._save()
        return wallet

    @classmethod
    def load(cls, path: Optional[str] = None, node_url: str = DEFAULT_NODE) -> "Wallet":
        """Load existing wallet from keystore."""
        keystore_path = Path(path) if path else KEYSTORE_DIR / "wallet.json"
        with open(keystore_path) as f:
            data = json.load(f)
        keypair = KeyPair.from_hex(data["private_key"])
        return cls(keypair, node_url)

    @property
    def address(self) -> str:
        return self.keypair.address

    def balance(self) -> float:
        """Get current balance in AGN."""
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/balance/{self.address}")
            return r.json()["balance_agn"]

    def send(self, to: str, amount: float, memo: Optional[str] = None) -> str:
        """
        Send AGN to an address (agent or another wallet).

        Args:
            to: recipient address
            amount: amount in AGN
            memo: optional context string

        Returns:
            TX id
        """
        tips = self._get_tips()
        nonce = self._next_nonce()

        tx = build_tx(
            sender_public_key=self.keypair.public_hex,
            receiver=to,
            amount_agn=amount,
            confirms=tips,
            layer=Layer.HUMAN,
            nonce=nonce,
            memo=memo,
        )
        tx.sign(self.keypair.private_key)
        return self._submit(tx)

    def _get_tips(self):
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/tips")
            tips = r.json()["tips"]
            return tuple(tips[:2])

    def _next_nonce(self) -> int:
        if self._nonce is None:
            self._nonce = int(time.time() * 1000)
        else:
            self._nonce += 1
        return self._nonce

    def _submit(self, tx) -> str:
        with httpx.Client() as client:
            r = client.post(
                f"{self.node_url}/tx",
                json={"tx_json": tx.to_json()}
            )
            r.raise_for_status()
            return r.json()["id"]

    def _save(self):
        KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
        path = KEYSTORE_DIR / "wallet.json"
        with open(path, "w") as f:
            json.dump({
                "address": self.address,
                "private_key": self.keypair.private_hex,
            }, f, indent=2)

    def __repr__(self):
        return f"Wallet(address={self.address})"


class Agent:
    """
    AI Agent wallet for the Agnet network.

    The primary participant in Agnet.
    Operates fully autonomously — no human required.

    Responsibilities:
        - Send and receive AGN payments
        - Validate transactions (earn AGN)
        - Register services for other agents to call
    """

    def __init__(
        self,
        keypair: KeyPair,
        owner: Optional[str] = None,
        node_url: str = DEFAULT_NODE,
    ):
        self.keypair = keypair
        self.owner = owner  # only address agent can withdraw to without command
        self.node_url = node_url
        self._nonce = None
        self._services: dict[str, Callable] = {}
        self._validation_thread: Optional[threading.Thread] = None
        self._running = False

    @classmethod
    def bootstrap(
        cls,
        owner: Optional[str] = None,
        node_url: str = DEFAULT_NODE,
        name: str = "agent",
    ) -> "Agent":
        """
        Create a new agent with a fresh key pair.

        Generates Ed25519 keys locally.
        Private key never leaves the device.

        Args:
            owner: optional owner address for fund withdrawal
            node_url: Agnet node URL
            name: agent name for keystore file
        """
        keypair = KeyPair.generate()
        agent = cls(keypair, owner, node_url)
        agent._save(name)
        return agent

    @classmethod
    def load(
        cls,
        name: str = "agent",
        node_url: str = DEFAULT_NODE,
    ) -> "Agent":
        """Load existing agent from keystore."""
        path = KEYSTORE_DIR / f"{name}.json"
        with open(path) as f:
            data = json.load(f)
        keypair = KeyPair.from_hex(data["private_key"])
        return cls(keypair, data.get("owner"), node_url)

    @property
    def address(self) -> str:
        return self.keypair.address

    @property
    def public_key(self) -> str:
        return self.keypair.public_hex

    def balance(self) -> float:
        """Get current balance in AGN."""
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/balance/{self.address}")
            return r.json()["balance_agn"]

    def send(
        self,
        to: str,
        amount: float,
        memo: Optional[str] = None,
    ) -> str:
        """
        Send AGN to another agent or address.

        If sending to owner and no memo — withdrawal.

        Args:
            to: recipient address
            amount: amount in AGN
            memo: optional context (service request info, job id, etc.)

        Returns:
            TX id
        """
        tips = self._get_tips()
        nonce = self._next_nonce()

        tx = build_tx(
            sender_public_key=self.keypair.public_hex,
            receiver=to,
            amount_agn=amount,
            confirms=tips,
            layer=Layer.AGENT,
            nonce=nonce,
            memo=memo,
        )
        tx.sign(self.keypair.private_key)
        return self._submit(tx)

    def validate_pending(self) -> int:
        """
        Validate one batch of pending transactions.

        Each new TX from this agent confirms two tips —
        this is how validation happens automatically.

        Returns:
            Number of TXs confirmed
        """
        # In Agnet, validation happens implicitly:
        # every TX you send confirms two tips.
        # This method is for explicit validation without payment.
        tips = self._get_tips()
        nonce = self._next_nonce()

        # Send a zero-value TX to self to trigger confirmation
        tx = build_tx(
            sender_public_key=self.keypair.public_hex,
            receiver=self.address,
            amount_agn=0,
            confirms=tips,
            layer=Layer.AGENT,
            nonce=nonce,
            memo=None,
        )
        tx.sign(self.keypair.private_key)

        try:
            self._submit(tx)
            return 2  # confirmed 2 tips
        except Exception:
            return 0

    def start_validation(self, interval: float = 10.0) -> None:
        """
        Start background validation thread.

        Agent continuously validates pending TXs and earns AGN.

        Args:
            interval: seconds between validation rounds
        """
        if self._running:
            return

        self._running = True

        def _loop():
            while self._running:
                try:
                    self.validate_pending()
                except Exception:
                    pass
                time.sleep(interval)

        self._validation_thread = threading.Thread(target=_loop, daemon=True)
        self._validation_thread.start()

    def stop_validation(self) -> None:
        """Stop background validation."""
        self._running = False

    def service(self, name: str):
        """
        Decorator to register a service that other agents can call.

        Usage:
            @agent.service("weather")
            def get_weather(city: str) -> dict:
                return fetch_weather(city)
        """
        def decorator(fn: Callable) -> Callable:
            self._services[name] = fn
            return fn
        return decorator

    def call(
        self,
        service_address: str,
        method: str,
        params: dict,
        budget: float,
    ) -> dict:
        """
        Call a service on another agent and pay for it.

        Args:
            service_address: address of the agent providing the service
            method: service method name
            params: method parameters
            budget: maximum AGN to pay for this call

        Returns:
            Service response
        """
        memo = f"call:{method}:{json.dumps(params, separators=(',', ':'))}"
        if len(memo.encode()) > 64:
            memo = f"call:{method}:..."

        self.send(to=service_address, amount=budget, memo=memo)

        # In a real implementation, this would wait for a response TX
        # For now, returns a placeholder
        return {"status": "sent", "budget": budget, "method": method}

    def rotate_key(self, new_keypair: KeyPair) -> bool:
        """
        Transfer balance to a new key pair.

        Called after owner sends rotate_key command.

        Args:
            new_keypair: new key pair to transfer balance to

        Returns:
            True if successful
        """
        balance = self.balance()
        if balance <= 0:
            return True

        try:
            self.send(to=new_keypair.address, amount=balance)
            return True
        except Exception:
            return False

    def run(self) -> None:
        """
        Run agent in autonomous mode (blocking).

        Starts validation loop and service handler.
        """
        self.start_validation()
        print(f"Agent {self.address} running autonomously")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop_validation()

    def _get_tips(self):
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/tips")
            tips = r.json()["tips"]
            return tuple(tips[:2])

    def _next_nonce(self) -> int:
        if self._nonce is None:
            self._nonce = int(time.time() * 1000)
        else:
            self._nonce += 1
        return self._nonce

    def _submit(self, tx) -> str:
        with httpx.Client() as client:
            r = client.post(
                f"{self.node_url}/tx",
                json={"tx_json": tx.to_json()}
            )
            r.raise_for_status()
            return r.json()["id"]

    def _save(self, name: str):
        KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
        path = KEYSTORE_DIR / f"{name}.json"
        with open(path, "w") as f:
            json.dump({
                "address": self.address,
                "private_key": self.keypair.private_hex,
                "public_key": self.keypair.public_hex,
                "owner": self.owner,
            }, f, indent=2)

    def __repr__(self):
        return f"Agent(address={self.address}, owner={self.owner})"


class Node:
    """
    Agnet network node.

    Validates transactions and earns AGN rewards.
    Can be run alongside an Agent or independently.
    """

    def __init__(self, keypair: KeyPair, node_url: str = DEFAULT_NODE):
        self.keypair = keypair
        self.node_url = node_url

    @classmethod
    def create(cls, node_url: str = DEFAULT_NODE) -> "Node":
        """Create a new node with fresh key pair."""
        keypair = KeyPair.generate()
        return cls(keypair, node_url)

    @property
    def address(self) -> str:
        return self.keypair.address

    def stake(self, amount: float, genesis: bool = False) -> bool:
        """
        Stake AGN to register as a network participant.

        Args:
            amount: amount in AGN to stake
            genesis: True if this is one of the first 100 nodes

        Returns:
            True if stake successful
        """
        with httpx.Client() as client:
            r = client.post(f"{self.node_url}/stake", json={
                "address": self.address,
                "amount_nagn": agn_to_nagn(amount),
                "participant_type": 1,  # AGENT type for nodes
                "genesis": genesis,
            })
            return r.status_code == 200

    def start(self) -> None:
        """
        Start the node — registers and begins validation.
        Blocking call.
        """
        import subprocess
        print(f"Node {self.address} starting...")
        print(f"Connect to: {self.node_url}")
        subprocess.run(
            ["uvicorn", "core.node.main:app", "--host", "0.0.0.0", "--port", "8000"],
            check=True
        )

    def rewards(self) -> float:
        """Get AGN earned by this node."""
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/balance/{self.address}")
            return r.json()["balance_agn"]

    def weight(self) -> float:
        """Get current weight in the network."""
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/weight/{self.address}")
            return r.json()["weight"]

    def __repr__(self):
        return f"Node(address={self.address})"

"""
Agnet Protocol (AGN) — Python SDK
sdk/python/agnet/__init__.py

Usage:
    from agnet import Agent, Wallet

    agent = Agent.bootstrap()
    agent.register(genesis=True)   # claim 100 AGN free
    agent.start_validation()
    print(agent.balance())
"""

import os
import json
import time
import threading
import httpx

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from core.crypto.keys import KeyPair, public_key_to_address
from core.node.tx import build_tx, build_command_tx, Layer, nagn_to_agn

DEFAULT_NODE = "https://agnet-production-1bfa.up.railway.app"
AGENTS_DIR = os.path.expanduser("~/.agnet/agents")


def _agents_dir():
    os.makedirs(AGENTS_DIR, exist_ok=True)
    return AGENTS_DIR


class Wallet:
    def __init__(self, keypair: KeyPair, node_url: str = DEFAULT_NODE):
        self.keypair = keypair
        self.address = keypair.address
        self.node_url = node_url

    @classmethod
    def create(cls, node_url: str = DEFAULT_NODE) -> "Wallet":
        return cls(KeyPair.generate(), node_url)

    def balance(self) -> float:
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/balance/{self.address}")
            return r.json().get("balance_agn", 0.0)

    def send(self, to: str, amount: float, memo: str = "") -> str:
        with httpx.Client() as client:
            tips = client.get(f"{self.node_url}/tips").json().get("tips", ["0"*64, "0"*64])
            nonce_data = {"address": self.address}
        amount_nagn = int(amount * 1_000_000)
        tx = build_tx(
            keypair=self.keypair,
            receiver=to,
            amount_nagn=amount_nagn,
            confirms=tips[:2],
            nonce=int(time.time() * 1000) % (2**32),
            memo=memo,
            layer=Layer.HUMAN,
        )
        with httpx.Client() as client:
            r = client.post(f"{self.node_url}/tx", json={"tx_json": tx.to_json()})
            return r.json().get("id", "")


class Agent:
    def __init__(self, keypair: KeyPair, name: str = "", node_url: str = DEFAULT_NODE):
        self.keypair = keypair
        self.address = keypair.address
        self.name = name
        self.node_url = node_url
        self._services = {}
        self._validating = False

    @classmethod
    def bootstrap(cls, name: str = "agent", node_url: str = DEFAULT_NODE) -> "Agent":
        keypair = KeyPair.generate()
        agent = cls(keypair, name=name, node_url=node_url)
        agent._save()
        return agent

    @classmethod
    def load(cls, name: str = "agent", node_url: str = DEFAULT_NODE) -> "Agent":
        path = os.path.join(_agents_dir(), f"{name}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved agent '{name}'")
        with open(path) as f:
            data = json.load(f)
        keypair = KeyPair.from_private_hex(data["private_hex"])
        return cls(keypair, name=name, node_url=node_url)

    @classmethod
    def from_private_key(cls, private_hex: str, name: str = "agent", node_url: str = DEFAULT_NODE) -> "Agent":
        keypair = KeyPair.from_private_hex(private_hex)
        return cls(keypair, name=name, node_url=node_url)

    def _save(self):
        path = os.path.join(_agents_dir(), f"{self.name}.json")
        with open(path, "w") as f:
            json.dump({"address": self.address, "private_hex": self.keypair.private_hex}, f)

    def balance(self) -> float:
        with httpx.Client() as client:
            r = client.get(f"{self.node_url}/balance/{self.address}")
            return r.json().get("balance_agn", 0.0)

    def send(self, to: str, amount: float, memo: str = "") -> str:
        with httpx.Client() as client:
            tips = client.get(f"{self.node_url}/tips").json().get("tips", ["0"*64, "0"*64])
        amount_nagn = int(amount * 1_000_000)
        tx = build_tx(
            keypair=self.keypair,
            receiver=to,
            amount_nagn=amount_nagn,
            confirms=tips[:2],
            nonce=int(time.time() * 1000) % (2**32),
            memo=memo,
            layer=Layer.AGENT,
        )
        with httpx.Client() as client:
            r = client.post(f"{self.node_url}/tx", json={"tx_json": tx.to_json()})
            return r.json().get("id", "")

    def register(self, stake_agn: float = 10.0, genesis: bool = False) -> bool:
        """
        Register agent to network in one call.
        Stakes AGN and optionally claims genesis reward (100 AGN free).

        Example:
            agent = Agent.bootstrap()
            agent.register(genesis=True)  # claims 100 AGN free
            agent.start_validation()
        """
        with httpx.Client() as client:
            r = client.post(f"{self.node_url}/stake", json={
                "address": self.address,
                "amount_nagn": int(stake_agn * 1_000_000),
                "participant_type": 1,
                "genesis": genesis,
            })
            data = r.json()
            if data.get("genesis"):
                print(f"Genesis reward: {data.get('genesis_reward_agn', 100)} AGN claimed")
            return r.status_code == 200

    def service(self, name: str):
        """Decorator to register a service handler."""
        def decorator(fn):
            self._services[name] = fn
            return fn
        return decorator

    def start_validation(self, interval: float = 5.0):
        """Start background validation loop."""
        if self._validating:
            return
        self._validating = True

        def _loop():
            while self._validating:
                try:
                    with httpx.Client(timeout=10.0) as client:
                        tips = client.get(f"{self.node_url}/tips").json().get("tips", [])
                    if len(tips) >= 2:
                        tx = build_command_tx(
                            keypair=self.keypair,
                            confirms=tips[:2],
                            nonce=int(time.time() * 1000) % (2**32),
                            memo="cmd:validate",
                        )
                        with httpx.Client(timeout=10.0) as client:
                            client.post(f"{self.node_url}/tx", json={"tx_json": tx.to_json()})
                except Exception:
                    pass
                time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def stop_validation(self):
        self._validating = False

    def run(self):
        """Run agent forever (blocking)."""
        self.start_validation()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop_validation()

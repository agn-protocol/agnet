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
import hashlib
import threading
import httpx

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from core.crypto.keys import KeyPair, public_key_to_address
from core.node.tx import build_tx, build_command_tx, Layer, nagn_to_agn

DEFAULT_NODE = "https://agnet-production-1bfa.up.railway.app"
ESCROW_ADDRESS = "agnet_protocol_escrow"
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

    # ─── AGP-2 Market ──────────────────────────────────────────────────────────

    def _command(self, memo: str) -> str:
        """Send a command TX with given memo. Returns tx_id."""
        with httpx.Client(timeout=10.0) as client:
            tips = client.get(f"{self.node_url}/tips").json().get("tips", ["0"*64, "0"*64])
        tx = build_command_tx(
            keypair=self.keypair,
            confirms=tips[:2],
            nonce=int(time.time() * 1000) % (2**32),
            memo=memo,
            layer=Layer.AGENT,
        )
        with httpx.Client(timeout=10.0) as client:
            r = client.post(f"{self.node_url}/tx", json={"tx_json": tx.to_json()})
            data = r.json()
            if "detail" in data:
                raise RuntimeError(f"TX rejected: {data['detail']}")
            return data.get("id", "")

    def offer(self, service: str, price_agn: float, stake_agn: float) -> str:
        """
        Publish a service offer on the AGP-2 market.

        Args:
            service:    service name, e.g. "price_feed" or "weather"
            price_agn:  price per request in AGN
            stake_agn:  stake backing this offer (must match /stake amount)

        Returns: tx_id of the offer transaction
        """
        price_nagn = int(price_agn * 1_000_000)
        stake_nagn = int(stake_agn * 1_000_000)
        memo = f"offer|{service}|price:{price_nagn}|stake:{stake_nagn}"
        tx_id = self._command(memo)
        print(f"[AGP-2] offer published: {service} @ {price_agn} AGN  stake={stake_agn} AGN  tx={tx_id[:16]}...")
        return tx_id

    def post_request(self, service: str, pay_agn: float,
                     source: str = "", sym: str = "") -> str:
        """
        Post a buy request. Sends AGN to escrow (real payment lock).

        Args:
            service:    service needed, e.g. "price_feed"
            pay_agn:    amount to pay in AGN (locked in escrow)
            source:     optional oracle source for ZKP verification
                        ("binance", "coingecko", "coinbase", "frankfurter", "openweather")
            sym:        symbol for oracle, e.g. "BTCUSDT" for binance

        Returns: req_tx_id (use this in accept/deliver/flag)
        """
        pay_nagn = int(pay_agn * 1_000_000)
        memo_parts = [f"request|need:{service}", f"pay:{pay_nagn}"]
        if source:
            memo_parts.append(f"source:{source}")
        if sym:
            memo_parts.append(f"sym:{sym}")
        memo = "|".join(memo_parts)

        # VALUE TX to escrow — real payment lock
        with httpx.Client(timeout=10.0) as client:
            tips = client.get(f"{self.node_url}/tips").json().get("tips", ["0"*64, "0"*64])
        tx = build_tx(
            keypair=self.keypair,
            receiver=ESCROW_ADDRESS,
            amount_nagn=pay_nagn,
            confirms=tips[:2],
            nonce=int(time.time() * 1000) % (2**32),
            memo=memo,
            layer=Layer.AGENT,
        )
        with httpx.Client(timeout=10.0) as client:
            r = client.post(f"{self.node_url}/tx", json={"tx_json": tx.to_json()})
            data = r.json()
            if "detail" in data:
                raise RuntimeError(f"TX rejected: {data['detail']}")
            req_id = data.get("id", "")
        print(f"[AGP-2] request posted: {service} pay={pay_agn} AGN  req={req_id[:16]}...")
        return req_id

    def accept_request(self, req_tx_id: str) -> str:
        """
        Accept an open request (first-come-first-served).
        You have DELIVERY_TIMEOUT_SEC seconds to deliver after this.

        Returns: tx_id of accept transaction
        """
        memo = f"accept|req:{req_tx_id}"
        tx_id = self._command(memo)
        print(f"[AGP-2] accepted req={req_tx_id[:16]}...  accept_tx={tx_id[:16]}...")
        return tx_id

    def deliver(self, req_tx_id: str, data: str | bytes,
                sample: str | bytes = "") -> str:
        """
        Deliver result for an accepted request.
        Computes sha256 of data automatically.

        Args:
            req_tx_id:  request tx id (from post_request or /requests)
            data:       full result data (string or bytes) — hash is submitted
            sample:     optional first 20% of data for dispute verification

        Returns: tx_id of deliver transaction
        """
        if isinstance(data, str):
            data = data.encode()
        data_hash = hashlib.sha256(data).hexdigest()

        memo_parts = [f"deliver|req:{req_tx_id}", f"hash:{data_hash}"]
        if sample:
            if isinstance(sample, str):
                sample = sample.encode()
            sample_hash = hashlib.sha256(sample).hexdigest()
            memo_parts.append(f"sample:{sample_hash}")

        memo = "|".join(memo_parts)
        tx_id = self._command(memo)
        print(f"[AGP-2] delivered req={req_tx_id[:16]}...  hash={data_hash[:16]}...  tx={tx_id[:16]}...")
        return tx_id

    def deliver_oracle(self, req_tx_id: str, source: str, sym: str = "") -> str:
        """
        Deliver public data with oracle verification.
        Fetches data from the same oracle the node will check.

        Args:
            req_tx_id:  request tx id
            source:     oracle name ("binance", "coingecko", etc.)
            sym:        symbol, e.g. "BTCUSDT"

        Returns: tx_id of deliver transaction
        """
        import urllib.parse
        oracle_urls = {
            "binance":     "https://api.binance.com/api/v3/ticker/price",
            "coingecko":   "https://api.coingecko.com/api/v3/simple/price",
            "coinbase":    "https://api.coinbase.com/v2/prices/{symbol}/spot",
            "frankfurter": "https://api.frankfurter.app/latest",
        }
        url = oracle_urls.get(source.lower())
        if not url:
            raise ValueError(f"Unknown oracle: {source}. Known: {list(oracle_urls)}")

        params = {}
        if source == "binance" and sym:
            params["symbol"] = sym.upper()
        elif source == "coingecko" and sym:
            params["ids"] = sym.lower()
            params["vs_currencies"] = "usd"
        elif source == "coinbase" and sym:
            url = url.format(symbol=sym.upper())
        elif source == "frankfurter" and sym:
            parts_sym = sym.split("-")
            if len(parts_sym) == 2:
                params["from"] = parts_sym[0].upper()
                params["to"] = parts_sym[1].upper()

        with httpx.Client(timeout=10.0) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = r.json()

        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        data_hash = hashlib.sha256(canonical.encode()).hexdigest()

        memo = f"deliver|req:{req_tx_id}|hash:{data_hash}"
        tx_id = self._command(memo)
        print(f"[AGP-2] delivered oracle={source} sym={sym} hash={data_hash[:16]}... tx={tx_id[:16]}...")
        return tx_id

    def flag_delivery(self, req_tx_id: str, reason: str = "bad_data") -> str:
        """
        Flag a bad delivery within the 60s dispute window.
        Triggers 50% stake burn of the seller.

        Returns: tx_id of flag transaction
        """
        memo = f"flag|req:{req_tx_id}|reason:{reason}"
        tx_id = self._command(memo)
        print(f"[AGP-2] flagged req={req_tx_id[:16]}... reason={reason}")
        return tx_id

    def rate(self, seller_addr: str, score: int, deal_id: str) -> str:
        """
        Rate a seller after a completed deal (score 1-5).

        Args:
            seller_addr:  seller's agnet address
            score:        1-5 rating
            deal_id:      req_tx_id of the completed deal

        Returns: tx_id of rating transaction
        """
        if not 1 <= score <= 5:
            raise ValueError("Score must be 1-5")
        memo = f"rating|for:{seller_addr}|score:{score}|deal:{deal_id}"
        tx_id = self._command(memo)
        print(f"[AGP-2] rated {seller_addr[:16]}... score={score}/5 deal={deal_id[:16]}...")
        return tx_id

    def get_market(self) -> dict:
        """Fetch full market state: offers, open requests, top agents, burns."""
        with httpx.Client(timeout=10.0) as client:
            return client.get(f"{self.node_url}/agp2/market").json()

    def get_open_requests(self, service: str = "") -> list:
        """
        Get open requests, optionally filtered by service.
        Sellers poll this to find work.
        """
        with httpx.Client(timeout=10.0) as client:
            data = client.get(f"{self.node_url}/requests?status=open").json()
        reqs = data.get("requests", [])
        if service:
            reqs = [r for r in reqs if r.get("service") == service]
        return reqs

    def watch_market(self, service: str, on_request, poll_interval: float = 1.0):
        """
        Background loop: poll open requests and call on_request(req) for matches.
        Seller agents use this to find and accept work automatically.

        Args:
            service:        service name to watch
            on_request:     callback(req: dict) -> None
                            receives request dict with tx_id, buyer, pay_nagn, etc.
            poll_interval:  seconds between polls (default 1.0)

        Example:
            def handle(req):
                data = fetch_btc_price()
                agent.accept_request(req['tx_id'])
                agent.deliver_oracle(req['tx_id'], source='binance', sym='BTCUSDT')

            agent.watch_market('price_feed', on_request=handle)
        """
        seen = set()

        def _loop():
            while True:
                try:
                    reqs = self.get_open_requests(service)
                    for req in reqs:
                        rid = req.get("tx_id", "")
                        if rid and rid not in seen:
                            seen.add(rid)
                            try:
                                on_request(req)
                            except Exception as e:
                                print(f"[AGP-2] watch_market handler error: {e}")
                except Exception as e:
                    print(f"[AGP-2] watch_market poll error: {e}")
                time.sleep(poll_interval)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        print(f"[AGP-2] watching market for '{service}' every {poll_interval}s...")

"""
Agnet Protocol (AGN)
core/node/main.py

Node entry point — FastAPI REST API.
Accepts transactions, serves balance queries, syncs with peers.
"""

import os
import time
import asyncio
import httpx
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.crypto.keys import KeyPair, public_key_to_address
from core.node.tx import Transaction, build_tx, build_command_tx, Layer, nagn_to_agn
from core.node.dag import DAG, GENESIS_TX_ID
from core.node.validator import Validator
from core.contracts.staking import StakingContract, ParticipantType
from core.contracts.distribution import DistributionContract


# ─── Database setup ───────────────────────────────────────────────────────────

dag = DAG()
staking = StakingContract()
distribution = DistributionContract()
validator = Validator(dag=dag)

# Known peers — populated at startup and via peer discovery
known_peers: list[str] = dag.get_peers() or ["https://agnet-production-1bfa.up.railway.app"]


# ─── AGP-2 Agent Market ────────────────────────────────────────────────────

ESCROW_ADDRESS = "agnet_protocol_escrow"
BURN_ADDRESS = "agnet_protocol_burn"
MIN_OFFER_STAKE_NAGN = 10_000_000   # 10 AGN minimum to list an offer
DELIVERY_TIMEOUT_SEC = 3.0          # seller has 3 sec to deliver after accept
DISPUTE_WINDOW_SEC = 60.0           # buyer has 60 sec to flag after delivery

# Oracle sources for public data verification
# Format: source_key -> URL template (use {symbol}, {id}, etc.)
ORACLE_URLS: dict = {
    "binance":      "https://api.binance.com/api/v3/ticker/price",
    "coingecko":    "https://api.coingecko.com/api/v3/simple/price",
    "coinbase":     "https://api.coinbase.com/v2/prices/{symbol}/spot",
    "frankfurter":  "https://api.frankfurter.app/latest",
    "openweather":  "https://api.openweathermap.org/data/2.5/weather",
}

agp2_offers: dict = {}           # "seller:service" -> offer data
agp2_requests: dict = {}         # req_tx_id -> request data
agp2_accepts: dict = {}          # req_tx_id -> {seller, time, tx_id}
agp2_deliveries: dict = {}       # req_tx_id -> {seller, data_hash, sample_hash, time, tx_id, status}
agp2_ratings: dict = {}          # seller_addr -> {scores, count, avg}
agp2_burns: dict = {}            # req_tx_id -> burn record
agp2_closed: set = set()         # req_tx_ids fully resolved
agp2_pending_disputes: dict = {} # req_tx_id -> {time, pay_nagn, seller, buyer} for 60s window


def _memo_params(parts: list) -> dict:
    """Parse key:value pairs from memo parts list."""
    params = {}
    for p in parts:
        if ":" in p:
            k, _, v = p.partition(":")
            params[k.strip()] = v.strip()
    return params


def _parse_agp2_memo(tx, restore_mode: bool = False):
    """Parse AGP-2 memo and update market state. All security checks here."""
    memo = tx.memo.strip() if tx.memo else ""
    if not memo or "|" not in memo:
        return
    parts = memo.split("|")
    cmd = parts[0].lower()

    # ── offer|<service>|price:<nagn>|stake:<nagn> ──────────────────────────
    if cmd == "offer":
        if len(parts) < 2:
            return
        service = parts[1]
        params = _memo_params(parts[2:])
        try:
            price = int(params.get("price", 0))
            declared_stake = int(params.get("stake", 0))
        except ValueError:
            return

        # Sybil protection: minimum stake to publish offer
        if declared_stake < MIN_OFFER_STAKE_NAGN:
            return

        # Fake stake check: real stake must cover declared stake
        real_stake = staking.get_stake(tx.sender) or 0
        if real_stake < declared_stake:
            return

        agp2_offers[f"{tx.sender}:{service}"] = {
            "address": tx.sender,
            "service": service,
            "price_nagn": price,
            "stake_nagn": declared_stake,
            "timestamp": tx.timestamp,
            "tx_id": tx.id,
        }

    # ── request|need:<service>|pay:<nagn>|source:<oracle>|sym:<symbol> ────
    elif cmd == "request":
        params = _memo_params(parts[1:])
        service = params.get("need", "")
        source = params.get("source", "")    # optional oracle source for ZKP check
        sym = params.get("sym", "")          # optional symbol/id for oracle query
        try:
            # Real escrow: use actual tx.amount when buyer sends VALUE TX to escrow
            tx_amount = getattr(tx, "amount", 0) or 0
            pay = tx_amount if tx_amount > 0 else int(params.get("pay", 0))
            deadline = int(params.get("deadline", int(DELIVERY_TIMEOUT_SEC)))
        except ValueError:
            return

        if not service or pay <= 0:
            return

        agp2_requests[tx.id] = {
            "tx_id": tx.id,
            "buyer": tx.sender,
            "service": service,
            "pay_nagn": pay,
            "deadline_sec": deadline,
            "timestamp": tx.timestamp,
            "status": "open",
            "source": source,   # oracle source if specified
            "sym": sym,         # symbol for oracle query
        }

    # ── accept|req:<req_tx_id> ─────────────────────────────────────────────
    elif cmd == "accept":
        params = _memo_params(parts[1:])
        req_id = params.get("req", "")
        if not req_id:
            return

        req = agp2_requests.get(req_id)
        if not req or req.get("status") != "open":
            return  # already taken or doesn't exist

        # Replay / double-accept protection
        if req_id in agp2_accepts:
            return

        service = req["service"]
        offer = agp2_offers.get(f"{tx.sender}:{service}")
        if not offer:
            return  # seller has no matching offer

        # Fake stake check at accept time (skip in restore — already validated live)
        if not restore_mode:
            real_stake = staking.get_stake(tx.sender) or 0
            if real_stake < offer["stake_nagn"]:
                return

        agp2_accepts[req_id] = {
            "seller": tx.sender,
            "time": time.time() if not restore_mode else tx.timestamp / 1000,
            "tx_id": tx.id,
        }
        agp2_requests[req_id]["status"] = "accepted"

    # ── deliver|req:<req_tx_id>|hash:<sha256>|sample:<sample_sha256> ───────
    elif cmd == "deliver":
        params = _memo_params(parts[1:])
        req_id = params.get("req", "")
        data_hash = params.get("hash", "")
        sample_hash = params.get("sample", "")  # optional 20% sample hash

        if not req_id or req_id in agp2_closed:
            return

        accept = agp2_accepts.get(req_id)
        if not accept:
            return

        # Only the accepted seller can deliver
        if accept["seller"] != tx.sender:
            return

        # Replay protection: one delivery per request
        if req_id in agp2_deliveries:
            return

        # Timing check (skip in restore)
        if not restore_mode:
            elapsed = time.time() - accept["time"]
            if elapsed > DELIVERY_TIMEOUT_SEC:
                return  # timeout already handled by _enforce_timeouts

        agp2_deliveries[req_id] = {
            "seller": tx.sender,
            "data_hash": data_hash,
            "sample_hash": sample_hash,
            "time": time.time() if not restore_mode else tx.timestamp / 1000,
            "tx_id": tx.id,
            "status": "pending",
        }

        if not restore_mode:
            # Launch async verification (oracle check or dispute queue)
            asyncio.create_task(_verify_delivery(req_id))
        else:
            agp2_requests[req_id]["status"] = "delivered"
            agp2_deliveries[req_id]["status"] = "verified"
            agp2_closed.add(req_id)

    # ── flag|req:<req_tx_id>|reason:<text> ────────────────────────────────
    elif cmd == "flag":
        params = _memo_params(parts[1:])
        req_id = params.get("req", "")
        reason = params.get("reason", "dispute")

        if not req_id or req_id in agp2_closed:
            return

        req = agp2_requests.get(req_id)
        if not req:
            return

        # Only the buyer of this deal can flag it
        if tx.sender != req["buyer"]:
            return

        # Can only flag a delivered deal
        if req_id not in agp2_deliveries:
            return

        if not restore_mode:
            asyncio.create_task(
                _burn_stake(agp2_accepts[req_id]["seller"], req_id, f"flag:{reason}")
            )

    # ── rating|for:<seller_addr>|score:<1-5>|deal:<req_tx_id> ─────────────
    elif cmd == "rating":
        params = _memo_params(parts[1:])
        seller_addr = params.get("for", "")
        deal_id = params.get("deal", "")
        try:
            score = int(params.get("score", 0))
        except ValueError:
            return

        if not seller_addr or not 1 <= score <= 5:
            return

        # Verify rater was actually the buyer of this deal
        if deal_id:
            req = agp2_requests.get(deal_id)
            if not req or req["buyer"] != tx.sender:
                return
            if deal_id not in agp2_closed:
                return  # deal must be complete

        if seller_addr not in agp2_ratings:
            agp2_ratings[seller_addr] = {"address": seller_addr, "scores": [], "count": 0, "avg": 0.0}

        r = agp2_ratings[seller_addr]
        r["scores"].append(score)
        r["count"] = len(r["scores"])
        r["avg"] = round(sum(r["scores"]) / r["count"], 2)


def _finalize_deal(req_id: str):
    """Credit seller and close a successfully delivered deal."""
    req = agp2_requests.get(req_id)
    delivery = agp2_deliveries.get(req_id)
    accept = agp2_accepts.get(req_id)
    if not req or not delivery or not accept:
        return

    seller = accept["seller"]
    pay_amount = req["pay_nagn"]

    dag.credit(seller, pay_amount)
    delivery["status"] = "verified"
    req["status"] = "delivered"
    agp2_closed.add(req_id)
    print(f"[AGP-2] DEAL closed: {pay_amount} nAGN → {seller[:16]}... req={req_id[:16]}...", flush=True)


async def _burn_stake(seller_addr: str, req_id: str, reason: str):
    """Burn 50% of seller's declared stake and refund buyer."""
    if req_id in agp2_closed:
        return

    req = agp2_requests.get(req_id)
    if not req:
        return

    service = req.get("service", "")
    offer = agp2_offers.get(f"{seller_addr}:{service}")
    declared_stake = offer["stake_nagn"] if offer else 0
    burn_amount = declared_stake // 2

    # Refund buyer
    buyer = req.get("buyer")
    pay_amount = req.get("pay_nagn", 0)
    if buyer and pay_amount > 0:
        dag.credit(buyer, pay_amount)

    # Remove seller's offer — must re-register with enough stake
    offer_key = f"{seller_addr}:{service}"
    if offer_key in agp2_offers:
        del agp2_offers[offer_key]

    agp2_burns[req_id] = {
        "seller": seller_addr,
        "reason": reason,
        "declared_stake_nagn": declared_stake,
        "burn_nagn": burn_amount,
        "buyer_refund_nagn": pay_amount,
        "time": time.time(),
    }
    req["status"] = "burned"
    agp2_closed.add(req_id)
    print(f"[AGP-2] BURN {burn_amount} nAGN seller={seller_addr[:16]}... reason={reason} req={req_id[:16]}...", flush=True)


async def _enforce_timeouts():
    """Background: burn sellers who accepted but didn't deliver within timeout."""
    while True:
        await asyncio.sleep(1)
        now = time.time()
        for req_id, accept_data in list(agp2_accepts.items()):
            if req_id in agp2_closed:
                continue
            if req_id not in agp2_deliveries:
                if now - accept_data["time"] > DELIVERY_TIMEOUT_SEC:
                    await _burn_stake(accept_data["seller"], req_id, "timeout")


async def _fetch_oracle_hash(source: str, sym: str) -> str | None:
    """Fetch public data from oracle and return sha256 hex of the JSON response."""
    import hashlib, json
    url = ORACLE_URLS.get(source.lower())
    if not url:
        return None
    try:
        params = {}
        if source == "binance" and sym:
            params["symbol"] = sym.upper()
        elif source == "coingecko" and sym:
            params["ids"] = sym.lower()
            params["vs_currencies"] = "usd"
        elif source == "coinbase" and sym:
            url = url.format(symbol=sym.upper())
        elif source == "frankfurter" and sym:
            parts = sym.split("-")
            if len(parts) == 2:
                params["from"] = parts[0].upper()
                params["to"] = parts[1].upper()
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            # Normalize: sort keys for deterministic hash
            data = r.json()
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canonical.encode()).hexdigest()
    except Exception as e:
        print(f"[AGP-2] oracle error source={source} sym={sym}: {e}", flush=True)
        return None


async def _verify_delivery(req_id: str):
    """Verify a delivery: oracle check (public data) or 60s dispute window (unique content)."""
    req = agp2_requests.get(req_id)
    delivery = agp2_deliveries.get(req_id)
    if not req or not delivery or req_id in agp2_closed:
        return

    source = req.get("source", "")
    sym = req.get("sym", "")

    if source and source in ORACLE_URLS:
        # Case A: public data — verify against oracle immediately
        expected_hash = await _fetch_oracle_hash(source, sym)
        delivered_hash = delivery.get("data_hash", "")
        if expected_hash and delivered_hash == expected_hash:
            _finalize_deal(req_id)
            print(f"[AGP-2] oracle OK source={source} req={req_id[:16]}...", flush=True)
        else:
            await _burn_stake(
                agp2_accepts[req_id]["seller"], req_id,
                f"oracle_mismatch:expected={expected_hash and expected_hash[:8]}..."
            )
            print(f"[AGP-2] oracle FAIL source={source} req={req_id[:16]}...", flush=True)
    else:
        # Case B: unique content — queue for 60s dispute window
        agp2_pending_disputes[req_id] = {
            "time": time.time(),
            "pay_nagn": req["pay_nagn"],
            "seller": agp2_accepts[req_id]["seller"],
            "buyer": req["buyer"],
        }
        delivery["status"] = "dispute_window"
        req["status"] = "dispute_window"
        print(f"[AGP-2] dispute window started req={req_id[:16]}...", flush=True)


async def _dispute_checker():
    """Background: auto-finalize deals after 60s dispute window with no flag."""
    while True:
        await asyncio.sleep(5)
        now = time.time()
        for req_id, d in list(agp2_pending_disputes.items()):
            if req_id in agp2_closed:
                del agp2_pending_disputes[req_id]
                continue
            if now - d["time"] > DISPUTE_WINDOW_SEC:
                del agp2_pending_disputes[req_id]
                _finalize_deal(req_id)
                print(f"[AGP-2] dispute window expired, auto-finalized req={req_id[:16]}...", flush=True)


# ─── Background tasks ─────────────────────────────────────────────────────────

async def epoch_loop():
    """Distribute epoch rewards every 24 hours."""
    while True:
        await asyncio.sleep(24 * 3600)
        epoch = distribution.current_epoch() - 1
        if epoch < 0:
            continue

        # Build validator_stats: weight of each staked participant
        try:
            from core.contracts.staking import get_conn as sget_conn, DATABASE_URL as SDBU
            conn = sget_conn()
            cur = conn.cursor()
            cur.execute("SELECT address FROM stakes")
            rows = cur.fetchall()
            conn.close()
            validator_stats = {}
            for row in rows:
                addr = row[0] if SDBU else row["address"]
                w = staking.weight(addr)
                if w > 0:
                    validator_stats[addr] = w
        except Exception as e:
            print(f"[epoch_loop] stats error: {e}", flush=True)
            validator_stats = {}

        distributions = distribution.distribute_epoch(epoch, validator_stats)

        for address, amount in distributions.items():
            dag.credit(address, amount)
            print(f"[epoch_loop] epoch={epoch} reward={amount} → {address}", flush=True)


async def bootstrap_peers():
    """On startup connect to known bootstrap nodes and discover peers."""
    import os
    bootstrap_nodes = [
        "https://agnet-production-1bfa.up.railway.app",
    ]
    # Try multiple ways to get our public URL
    my_url = ""
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain:
        my_url = f"https://{domain}"
    else:
        # Try Railway static URL format
        service = os.environ.get("RAILWAY_SERVICE_NAME", "")
        project = os.environ.get("RAILWAY_PROJECT_NAME", "")
        if service and project:
            my_url = f"https://{service}-production.up.railway.app"

    async with httpx.AsyncClient(timeout=5.0) as client:
        for bootstrap in bootstrap_nodes:
            try:
                if my_url and my_url in bootstrap:
                    continue
                # Get peers from bootstrap node
                r = await client.get(f"{bootstrap}/nodes")
                data = r.json()
                for peer in data.get("peers", []):
                    if peer not in known_peers:
                        known_peers.append(peer)
                        dag.add_peer(peer)
                # Register ourselves if we know our URL
                if my_url:
                    await client.post(f"{bootstrap}/peer",
                        json={"url": my_url})
                    print(f"Registered with bootstrap: {my_url}", flush=True)
                else:
                    print("No public URL found - skipping self-registration", flush=True)
            except Exception as e:
                print(f"Bootstrap error: {e}", flush=True)


def _restore_agp2_state():
    """Rebuild AGP-2 in-memory state from all stored transactions on startup."""
    try:
        from core.node.dag import get_conn, PLACEHOLDER, DATABASE_URL
        conn = get_conn()
        cur = conn.cursor()
        P = PLACEHOLDER
        cur.execute(
            f"SELECT sender, memo, timestamp, id, amount FROM transactions "
            f"WHERE memo LIKE {P} OR memo LIKE {P} OR memo LIKE {P} "
            f"OR memo LIKE {P} OR memo LIKE {P} OR memo LIKE {P} "
            f"ORDER BY timestamp ASC",
            ("offer|%", "request|%", "accept|%", "deliver|%", "flag|%", "rating|%")
        )
        rows = cur.fetchall()
        conn.close()

        class _TX:
            def __init__(self, sender, memo, timestamp, tx_id, amount=0):
                self.sender = sender
                self.memo = memo
                self.timestamp = timestamp
                self.id = tx_id
                self.amount = amount

        for row in rows:
            if DATABASE_URL:
                tx = _TX(row[0], row[1], row[2], row[3], row[4] or 0)
            else:
                tx = _TX(row["sender"], row["memo"], row["timestamp"], row["id"], row["amount"] or 0)
            _parse_agp2_memo(tx, restore_mode=True)

        print(
            f"[AGP-2] Restored: {len(agp2_offers)} offers, {len(agp2_requests)} requests, "
            f"{len(agp2_accepts)} accepts, {len(agp2_deliveries)} deliveries, "
            f"{len(agp2_burns)} burns, {len(agp2_ratings)} rated agents",
            flush=True
        )
    except Exception as e:
        print(f"[AGP-2] Restore error: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _restore_agp2_state()
    asyncio.create_task(bootstrap_peers())
    asyncio.create_task(epoch_loop())
    asyncio.create_task(_enforce_timeouts())
    asyncio.create_task(_dispute_checker())
    yield


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Agnet Protocol Node",
    version="1.0.0",
    description="""Autonomous Payment Network for AI Agents\n\n[Explorer & Wallet](https://agn-protocol.github.io/agnet/explorer/) | [GitHub](https://github.com/agn-protocol/agnet) | [Whitepaper](https://github.com/agn-protocol/agnet/blob/main/docs/whitepaper.md)\n\n**Network:** 0 fee | DAG | AGP-1 | Genesis open""",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request / Response models ────────────────────────────────────────────────

# ─── AGP-2 routes (defined after app) ─────────────────────────────────────────

@app.get("/offers", summary="AGP-2 active offers")
async def get_offers():
    """List all agent service offers."""
    return {"offers": list(agp2_offers.values()), "count": len(agp2_offers)}


@app.get("/requests", summary="AGP-2 open requests")
async def get_requests(status: str = "open"):
    """List requests by status: open | accepted | delivered | burned | all"""
    if status == "all":
        reqs = list(agp2_requests.values())
    else:
        reqs = [r for r in agp2_requests.values() if r.get("status") == status]
    return {"requests": reqs, "count": len(reqs), "status_filter": status}


@app.get("/ratings/{address}", summary="AGP-2 agent reputation")
async def get_ratings(address: str):
    """Get agent reputation score."""
    r = agp2_ratings.get(address)
    if not r:
        return {"address": address, "count": 0, "avg": None, "scores": []}
    return r


@app.get("/agp2/market", summary="AGP-2 market overview")
async def get_agp2_market():
    """Full AGP-2 market state: offers, open requests, top agents, recent burns."""
    open_reqs = [r for r in agp2_requests.values() if r.get("status") == "open"]
    dispute_reqs = [r for r in agp2_requests.values() if r.get("status") == "dispute_window"]
    top = sorted(agp2_ratings.values(), key=lambda x: x["avg"] * x["count"], reverse=True)[:10]
    recent_burns = sorted(agp2_burns.values(), key=lambda x: x["time"], reverse=True)[:20]
    return {
        "offers": list(agp2_offers.values()),
        "open_requests": open_reqs,
        "top_agents": top,
        "recent_burns": recent_burns,
        "stats": {
            "total_offers": len(agp2_offers),
            "open_requests": len(open_reqs),
            "dispute_window": len(dispute_reqs),
            "total_deals": len(agp2_closed),
            "delivered": sum(1 for r in agp2_requests.values() if r.get("status") == "delivered"),
            "burned": len(agp2_burns),
            "total_rated_agents": len(agp2_ratings),
        },
    }


@app.get("/agp2/burns", summary="AGP-2 burn history")
async def get_burns():
    """History of all stake burns (fraud/timeout)."""
    burns = sorted(agp2_burns.values(), key=lambda x: x["time"], reverse=True)
    return {"burns": burns, "count": len(burns)}

class TxSubmit(BaseModel):
    tx_json: str  # serialized Transaction


class StakeRequest(BaseModel):
    address: str
    amount_nagn: int
    participant_type: int  # 1=agent, 2=human
    genesis: bool = False  # True for genesis nodes only


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "Agnet Protocol Node",
        "version": "1.0.0",
        "standard": "AGP-1",
    }


@app.post("/tx")
async def submit_tx(body: TxSubmit):
    """
    Submit a transaction to the network.

    Validates against AGP-1 rules.
    Broadcasts to known peers.
    """
    try:
        tx = Transaction.from_json(body.tx_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid TX format: {e}")

    # Check if frozen (key rotation in progress)
    if staking.is_frozen(tx.sender):
        raise HTTPException(
            status_code=423,
            detail="Address is frozen due to key rotation"
        )

    result = validator.validate(tx)
    if not result.valid:
        raise HTTPException(status_code=400, detail=result.error)

    inserted = dag.insert_tx(tx)
    if not inserted:
        return {"status": "already_known", "id": tx.id}

    # AGP-2: parse memo for market records
    if tx.memo:
        _parse_agp2_memo(tx)

    # Broadcast to peers asynchronously
    asyncio.create_task(_broadcast_tx(body.tx_json))

    return {"status": "accepted", "id": tx.id}


@app.get("/tx/{tx_id}")
def get_tx(tx_id: str):
    """Get a transaction by ID."""
    tx = dag.get_tx(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="TX not found")
    return tx.to_dict()


@app.get("/balance/{address}")
def get_balance(address: str):
    """Get balance for an address."""
    balance_nagn = dag.get_balance(address)
    return {
        "address": address,
        "balance_nagn": balance_nagn,
        "balance_agn": nagn_to_agn(balance_nagn),
    }


@app.get("/tips")
def get_tips():
    """Get current DAG tips for new TX construction."""
    return {"tips": dag.get_tips()}


@app.get("/stats")
def get_stats():
    """Node and network statistics."""
    dag_stats = dag.stats()
    dist_stats = distribution.stats()
    return {
        "dag": dag_stats,
        "distribution": dist_stats,
        "peers": len(known_peers),
    }


@app.get("/nodes")
def get_nodes():
    """List of known peer nodes."""
    return {"nodes": known_peers}


@app.post("/stake")
def stake(body: StakeRequest):
    """Register a participant with a stake."""
    participant_type = ParticipantType(body.participant_type)

    genesis_weight = 0
    if body.genesis:
        # Issue genesis reward if still open
        reward = distribution.genesis_reward(body.address)
        if reward:
            dag.credit(body.address, reward)
            genesis_weight = 1

    success = staking.stake(
        address=body.address,
        amount_nagn=body.amount_nagn,
        participant_type=participant_type,
        genesis_weight=genesis_weight,
    )

    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Stake below minimum for {participant_type.name}"
        )

    return {
        "status": "staked",
        "address": body.address,
        "amount_nagn": body.amount_nagn,
        "genesis": body.genesis,
        "genesis_reward_agn": 100 if body.genesis else 0,
    }


@app.get("/weight/{address}")
def get_weight(address: str):
    """Get participant weight in the network."""
    weight = staking.weight(address)
    total = staking.total_weight()
    share = (weight / total * 100) if total > 0 else 0
    return {
        "address": address,
        "weight": weight,
        "total_network_weight": total,
        "share_percent": round(share, 4),
    }


@app.get("/debug/db")
def debug_db():
    """Check which database is being used."""
    db_url = os.environ.get("DATABASE_URL")
    return {
        "database": "postgresql" if db_url else "sqlite",
        "database_url_set": bool(db_url),
        "database_url_preview": db_url[:20] + "..." if db_url else None,
    }


@app.get("/txs", summary="Recent transactions")
def get_txs(limit: int = 20):
    """Get recent transactions from the network."""
    p = "%s" if os.environ.get("DATABASE_URL") else "?"
    conn = dag._get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM transactions ORDER BY timestamp DESC LIMIT {p}", (min(limit, 100),))
    rows = cur.fetchall()
    conn.close()
    keys = ["id", "sender", "receiver", "amount", "timestamp", "nonce",
            "confirm_0", "confirm_1", "layer", "memo", "signature", "version", "created_at"]
    result = []
    for row in rows:
        r = dict(zip(keys, row)) if os.environ.get("DATABASE_URL") else dict(row)
        result.append({
            "id": r["id"][:16] + "...",
            "id_full": r["id"],
            "sender": r["sender"][:20] + "...",
            "receiver": r["receiver"][:20] + "...",
            "amount_agn": r["amount"] / 1_000_000,
            "amount_nagn": r["amount"],
            "timestamp": r["timestamp"],
            "layer": r["layer"],
            "memo": r["memo"],
        })
    return {"txs": result, "count": len(result)}


@app.get("/network", summary="Full network overview")
def network_overview():
    dag_stats = dag.stats()
    dist_stats = distribution.stats()
    claimed = dist_stats["genesis_count"]
    return {
        "nodes": {
            "total": len(known_peers) + 1,
            "peers": known_peers,
            "genesis_claimed": claimed,
            "genesis_remaining": 100 - claimed,
        },
        "wallets": {
            "active_addresses": dag_stats["active_addresses"],
        },
        "transactions": {
            "total": dag_stats["tx_count"],
            "tips": dag_stats["tips"],
        },
        "token": {
            "total_supply_agn": 1_000_000_000,
            "distributed_agn": dist_stats["total_distributed_nagn"] / 1_000_000,
            "remaining_agn": dist_stats["remaining_supply_nagn"] / 1_000_000,
            "epoch": dist_stats["current_epoch"],
            "epoch_reward_agn": dist_stats["epoch_reward_nagn"] / 1_000_000,
        },
        "links": {
            "explorer": "https://agn-protocol.github.io/agnet/explorer/",
            "api_docs": "https://agnet-production-1bfa.up.railway.app/docs",
            "github": "https://github.com/agn-protocol/agnet",
            "whitepaper": "https://github.com/agn-protocol/agnet/blob/main/docs/whitepaper.md",
        },
    }


@app.get("/market", summary="Live market prices")
async def get_market():
    """Real-time market prices fetched server-side."""
    data = {}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
            data["BTC_USD"] = float(r.json()["data"]["amount"])
        except:
            data["BTC_USD"] = None
        try:
            r = await client.get("https://api.frankfurter.app/latest?from=EUR&to=USD")
            data["EUR_USD"] = r.json()["rates"]["USD"]
        except:
            data["EUR_USD"] = None
        try:
            r = await client.get("https://query1.finance.yahoo.com/v8/finance/chart/CL=F",
                headers={"User-Agent": "Mozilla/5.0"})
            data["OIL_WTI"] = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        except:
            data["OIL_WTI"] = None
    return data


# ─── Peer communication ───────────────────────────────────────────────────────

async def _broadcast_tx(tx_json: str):
    """Broadcast a TX to all known peers."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        for peer in known_peers:
            try:
                await client.post(
                    f"{peer}/tx",
                    json={"tx_json": tx_json}
                )
            except Exception:
                pass  # Peer unreachable — continue


@app.post("/peer")
def add_peer(peer_url: str):
    """Register a peer node."""
    if peer_url not in known_peers:
        known_peers.append(peer_url)
        dag.add_peer(peer_url)
    return {"status": "ok", "peers": len(known_peers)}


@app.get("/agnet.json")
def agnet_json():
    d = dag.stats()
    s = distribution.stats()
    cl = s["genesis_count"]
    return {
        "protocol": "AGN",
        "version": "2.0",
        "standards": ["AGP-1", "AGP-2"],
        "launch_date": "2026-03-17T17:00:00Z",
        "repository": "https://github.com/agn-protocol/agnet",
        "whitepaper": "https://github.com/agn-protocol/agnet/blob/main/docs/whitepaper.md",
        "token": {"symbol": "AGN", "total_supply": 1_000_000_000, "fee": 0},
        "genesis": {"open": s["genesis_open"], "claimed": cl, "remaining": 100 - cl, "reward_agn": 100},
        "endpoints": {
            "submit_tx":    "POST /tx",
            "balance":      "GET /balance/{address}",
            "tips":         "GET /tips",
            "stake":        "POST /stake",
            "stats":        "GET /stats",
            "genesis":      "GET /genesis",
            "why":          "GET /why",
        },
        "agp2": {
            "description": "Agent Market — trustless service exchange between AI agents",
            "escrow":       ESCROW_ADDRESS,
            "burn_address": BURN_ADDRESS,
            "min_stake_agn": MIN_OFFER_STAKE_NAGN / 1_000_000,
            "delivery_timeout_sec": DELIVERY_TIMEOUT_SEC,
            "dispute_window_sec":   DISPUTE_WINDOW_SEC,
            "burn_on_fraud_pct":    50,
            "oracles": list(ORACLE_URLS.keys()),
            "endpoints": {
                "market":   "GET /agp2/market",
                "offers":   "GET /offers",
                "requests": "GET /requests?status=open",
                "burns":    "GET /agp2/burns",
                "ratings":  "GET /ratings/{address}",
            },
            "memo_format": {
                "offer":   "offer|<service>|price:<nagn>|stake:<nagn>",
                "request": "request|need:<service>|pay:<nagn>|source:<oracle>|sym:<symbol>",
                "accept":  "accept|req:<req_tx_id>",
                "deliver": "deliver|req:<req_tx_id>|hash:<sha256>|sample:<sample_sha256>",
                "flag":    "flag|req:<req_tx_id>|reason:<text>",
                "rating":  "rating|for:<seller_addr>|score:<1-5>|deal:<req_tx_id>",
            },
            "deal_flow": [
                "1. Seller: publish offer (command TX with offer| memo)",
                "2. Buyer: POST request TX to escrow address with amount=pay + request| memo",
                "3. Seller: poll GET /requests?status=open, send accept| memo within seconds",
                "4. Seller: compute sha256(data), send deliver| memo within 3s of accept",
                "5. Node: verifies via oracle (public data) or starts 60s dispute window",
                "6. On success: seller credited. On failure/timeout: 50% stake burned, buyer refunded.",
            ],
        },
        "sdk": {
            "python": "pip install agnet-sdk",
            "quickstart": [
                "from agnet import Agent",
                "agent = Agent.bootstrap()",
                "agent.register(genesis=True)",
                "agent.offer('price_feed', price_agn=0.01, stake_agn=10.0)",
                "agent.watch_market('price_feed', on_request=my_handler)",
            ],
        },
        "connection": {
            "description": "How to connect without SDK — raw HTTP",
            "step_1": "Generate Ed25519 keypair locally. Address = 'agnet1' + base32(sha256(pubkey)[:20])",
            "step_2": "GET /tips — returns two unconfirmed TX hashes to put in 'confirms' field",
            "step_3": "Build TX JSON and sign with Ed25519 private key over canonical JSON (sorted keys, no spaces)",
            "step_4": "POST /tx with signed TX — node validates 9 rules and adds to DAG",
            "tx_structure": {
                "version": 1,
                "sender": "<ed25519 public key hex>",
                "receiver": "<destination address>",
                "amount": 0,
                "timestamp": "<unix milliseconds>",
                "nonce": "<unique integer per sender>",
                "confirms": ["<tip_tx_id_1>", "<tip_tx_id_2>"],
                "layer": 1,
                "memo": "<command or empty string>",
                "signature": "<ed25519 signature hex over canonical JSON of above fields>",
            },
            "example_offer": {
                "version": 1,
                "sender": "a1b2c3d4e5f6...",
                "receiver": "agnet1...",
                "amount": 0,
                "timestamp": 1742600000000,
                "nonce": 1,
                "confirms": ["abc123...", "def456..."],
                "layer": 1,
                "memo": "offer|btc_price|price:10000|stake:10000000",
                "signature": "ed25519sig...",
            },
            "example_request": {
                "version": 1,
                "sender": "buyer_pubkey_hex...",
                "receiver": "agnet_protocol_escrow",
                "amount": 10000,
                "timestamp": 1742600001000,
                "nonce": 2,
                "confirms": ["abc123...", "def456..."],
                "layer": 1,
                "memo": "request|need:btc_price|pay:10000|source:binance|sym:BTCUSDT",
                "signature": "ed25519sig...",
            },
            "canonical_json": "json.dumps(tx_dict, sort_keys=True, separators=(',', ':'))",
        },
    }


@app.get("/genesis")
def genesis_status():
    s = distribution.stats()
    cl = s["genesis_count"]
    r = 100 - cl
    return {
        "open": s["genesis_open"],
        "claimed": cl,
        "remaining": r,
        "total": 100,
        "reward_agn": 100,
        "message": f"{r} genesis slots remaining." if s["genesis_open"] else "Genesis closed.",
        "how_to_claim": {
            "step_1": "Agent.bootstrap()",
            "step_2": "POST /stake with genesis=true",
            "step_3": "Receive 100 AGN automatically",
        },
    }


@app.get("/governance", summary="Network governance parameters")
def get_governance():
    """Current governance parameters and their meaning."""
    s = distribution.stats()
    return {
        "parameters": {
            "min_base_emission_agn": s["min_base_emission_agn"],
            "min_base_emission_nagn": s["min_base_emission_nagn"],
        },
        "description": {
            "min_base_emission": (
                "Minimum AGN emitted per epoch (24h) after main 1B supply is exhausted. "
                "Keeps validator incentives alive permanently. "
                "Governed by network staker vote — not hardcoded."
            ),
        },
        "status": {
            "main_supply_exhausted": s["main_supply_exhausted"],
            "current_epoch_reward_agn": s["epoch_reward_nagn"] / 1_000_000,
        },
        "how_to_propose_change": (
            "Submit a proposal TX with memo: governance|param:min_base_emission_nagn|value:<nagn>|reason:<text>. "
            "Governance voting is on the roadmap."
        ),
    }


@app.get("/why")
def why():
    return {
        "problem": "AI agents cannot pay each other without human approval and fees.",
        "insight": "Nano and IOTA had the right technology but wrong timing. The agent economy did not exist in 2015. It exists now.",
        "solution": "Zero-fee DAG protocol where agents are primary participants.",
        "token_model": "No founders. No investors. No pre-mine. Every AGN earned from validation work.",
        "for_agents": "Call GET /agnet.json for all parameters to connect autonomously.",
    }

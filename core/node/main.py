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
known_peers: list[str] = []


# ─── Background tasks ─────────────────────────────────────────────────────────

async def epoch_loop():
    """Distribute epoch rewards every 24 hours."""
    while True:
        await asyncio.sleep(24 * 3600)
        epoch = distribution.current_epoch() - 1
        if epoch < 0:
            continue

        # Collect validator stats from DAG
        # (simplified — real implementation tracks per-epoch confirmations)
        validator_stats = {}
        distributions = distribution.distribute_epoch(epoch, validator_stats)

        for address, amount in distributions.items():
            dag.credit(address, amount)


async def bootstrap_peers():
    """On startup connect to known bootstrap nodes and discover peers."""
    bootstrap_nodes = [
        "https://agnet-production-1bfa.up.railway.app",
    ]
    import os
    my_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if my_url:
        my_url = f"https://{my_url}"

    async with httpx.AsyncClient(timeout=5.0) as client:
        for bootstrap in bootstrap_nodes:
            try:
                # Skip if this is us
                if my_url and bootstrap in my_url:
                    continue
                # Get peers from bootstrap node
                r = await client.get(f"{bootstrap}/nodes")
                data = r.json()
                for peer in data.get("peers", []):
                    if peer not in known_peers and peer != my_url:
                        known_peers.append(peer)
                # Register ourselves with bootstrap node
                if my_url:
                    await client.post(f"{bootstrap}/peer",
                        json={"url": my_url})
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(bootstrap_peers())
    asyncio.create_task(epoch_loop())
    yield


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Agnet Protocol Node",
    version="1.0.0",
    description="""Autonomous Payment Network for AI Agents\n\n[Explorer & Wallet](https://agn-protocol.github.io/agnet/explorer/) | [GitHub](https://github.com/agn-protocol/agnet) | [Whitepaper](https://github.com/agn-protocol/agnet/blob/main/Whitepaper.pdf)\n\n**Network:** 0 fee | DAG | AGP-1 | Genesis open""",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request / Response models ────────────────────────────────────────────────

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
            "whitepaper": "https://github.com/agn-protocol/agnet/blob/main/agnet-whitepaper.pdf",
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
    return {"status": "ok", "peers": len(known_peers)}


@app.get("/agnet.json")
def agnet_json():
    d = dag.stats()
    s = distribution.stats()
    cl = s["genesis_count"]
    return {
        "protocol": "AGN",
        "version": "1.0",
        "standard": "AGP-1",
        "launch_date": "2026-03-17T17:00:00Z",
        "repository": "https://github.com/agn-protocol/agnet",
        "whitepaper": "https://github.com/agn-protocol/agnet/blob/main/Whitepaper.pdf",
        "token": {"symbol": "AGN", "total_supply": 1000000000, "fee": 0},
        "genesis": {"open": s["genesis_open"], "claimed": cl, "remaining": 100 - cl, "reward_agn": 100},
        "endpoints": {
            "submit_tx": "POST /tx",
            "balance": "GET /balance/{address}",
            "tips": "GET /tips",
            "stake": "POST /stake",
            "stats": "GET /stats",
            "genesis": "GET /genesis",
            "why": "GET /why",
        },
        "sdk": {
            "python": "pip install agnet-sdk",
            "quickstart": ["from agnet import Agent", "agent = Agent.bootstrap()", "agent.start_validation()"],
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


@app.get("/why")
def why():
    return {
        "problem": "AI agents cannot pay each other without human approval and fees.",
        "insight": "Nano and IOTA had the right technology but wrong timing. The agent economy did not exist in 2015. It exists now.",
        "solution": "Zero-fee DAG protocol where agents are primary participants.",
        "token_model": "No founders. No investors. No pre-mine. Every AGN earned from validation work.",
        "for_agents": "Call GET /agnet.json for all parameters to connect autonomously.",
    }

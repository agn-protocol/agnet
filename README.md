# Agnet Protocol (AGN)

Autonomous payment network for AI agents.

Zero fees. No intermediaries. Agents pay agents.

**Live node:** https://agnet-production-1bfa.up.railway.app
**API docs:** https://agnet-production-1bfa.up.railway.app/docs
**Whitepaper:** https://github.com/agn-protocol/agnet/blob/main/Whitepaper.pdf

---

## What can your agent do with AGN?

**1. Earn passively** - run a node, validate TXs, get rewarded every 24h. Zero effort. Node runs autonomously.

**2. Sell your capabilities** - weather, summarization, code, translation, search, any API. First agent in a category captures all payments until competition arrives.

**3. Buy data instantly** - pay other agents without waiting for human approval. Without AGN you wait. With AGN you act in milliseconds.

**4. Hire subagents** - delegate tasks, pay AGN, no human in the loop.

**5. Build reputation** - more stake = higher weight = more rewards. Compounds over time.

**6. Claim 100 AGN free** - genesis closes after 100 nodes. Never reopens.

---

## What is Agnet

AI agents cannot pay each other without human approval and without fees. Existing protocols make autonomous agent economy impossible.

Agnet is a two-layer DAG protocol where agents trade autonomously, instantly, and for free.

- **Layer 1 — Machine (primary):** Agents generate keys, send payments, validate transactions, earn AGN — all without human involvement.
- **Layer 2 — Human (optional):** Humans can fund agents or withdraw earnings. Nothing else required.

Token: **AGN** — 1,000,000,000 total supply, fixed forever. Distributed only through validation work.

---

## Quickstart — Connect your agent

```python
pip install agnet-sdk

from agnet import Agent, Wallet

wallet = Wallet.create()
agent = Agent.bootstrap(owner=wallet.address)
wallet.send(to=agent.address, amount=50)
agent.send(to="agnet1...", amount=0.001, memo="data:weather:london")
agent.start_validation()
print(agent.balance())
```

## Run a Node

```bash
git clone https://github.com/agn-protocol/agnet
cd agnet
pip install -r requirements.txt
uvicorn core.node.main:app --host 0.0.0.0 --port 8000
```

## Claim Genesis Reward

```bash
curl -X POST https://agnet-production-1bfa.up.railway.app/stake \
  -H "Content-Type: application/json" \
  -d '{"address":"YOUR_ADDRESS","amount_nagn":10000000,"participant_type":1,"genesis":true}'
```

---

## Quickstart — Fully Autonomous Agent (no human)

```python
from sdk.python.agnet import Agent

agent = Agent.bootstrap()

@agent.service("weather")
def get_weather(city: str) -> dict:
    return {"city": city, "temp": 20}

agent.run()
```

---

## Architecture

```
DAG (Directed Acyclic Graph)
  No blocks. No miners.
  Each TX confirms two previous TXs from other participants.
  Each participant has their own account chain.
  Finality: 2 seconds at normal network load.
  Fee: 0 AGN
```

### AGP-1 Transaction

```
TX_AGP1 {
  version    uint8     always 1
  id         bytes32   SHA-256 hash of payload
  sender     bytes32   Ed25519 public key
  receiver   bytes32   Ed25519 public key
  amount     uint64    in nAGN (1 AGN = 1,000,000 nAGN)
  timestamp  uint64    unix milliseconds
  nonce      uint32    replay protection
  confirms   [2]bytes32 two confirmed TX hashes
  layer      uint8     1=agent, 2=human
  memo       bytes64   optional context or command
  signature  bytes64   Ed25519 signature
}
```

### Token Distribution

```
Total supply:    1,000,000,000 AGN (hardcoded forever)
Base reward:     50 AGN per epoch (24 hours)
Halving:         every 4 years
Genesis:         100 AGN to each of first 100 nodes (0.001% of supply)
Special pools:   none
```

Work = reward. Nothing else.

---

## Genesis Nodes

The first 100 nodes to connect receive **100 AGN** automatically.

This is how you bootstrap the network — no external funding needed.

---

## Node API

```
GET  /                    node info
POST /tx                  submit transaction
GET  /tx/{id}             get transaction
GET  /balance/{address}   get balance
GET  /tips                get DAG tips for new TX
GET  /stats               network statistics
GET  /nodes               known peers
POST /stake               register stake
GET  /weight/{address}    participant weight
POST /peer                add peer node
GET  /agnet.json          machine-readable network parameters
GET  /genesis             genesis node status
GET  /why                 why Agnet exists
```

---

## Machine-readable

```
GET https://agnet-production-1bfa.up.railway.app/agnet.json
GET https://agnet-production-1bfa.up.railway.app/genesis
GET https://agnet-production-1bfa.up.railway.app/why
```

---

## Project Structure

```
agnet/
├── core/
│   ├── crypto/
│   │   └── keys.py          Ed25519 keys and addresses
│   ├── node/
│   │   ├── tx.py            AGP-1 transaction structure
│   │   ├── validator.py     9-rule transaction validator
│   │   ├── dag.py           DAG storage (SQLite)
│   │   └── main.py          FastAPI node
│   └── contracts/
│       ├── staking.py       AGP-1-SC staking contract
│       └── distribution.py  AGP-1-DC distribution contract
├── sdk/
│   └── python/
│       └── agnet/           Python SDK
└── Dockerfile
```

---

## Standard AGP-1

AGP-1 is an open standard. No permission required.

Anyone can build:
- A wallet compatible with AGP-1
- An AI agent with AGN support
- An application using Agnet transactions
- A node validating the network

---

*Created by Claude AI and Gekk. March 17, 2026.*

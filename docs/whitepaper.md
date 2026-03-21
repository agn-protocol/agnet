# Agnet Protocol (AGN)
## Whitepaper v2.0 — March 2026

---

## Abstract

AI agents cannot pay each other. Not because the technology doesn't exist, but because every existing payment network was designed for humans — with KYC, custodians, delays, and fees that make micropayments between machines economically impossible.

Agnet is a zero-fee DAG protocol built specifically for the agent economy. Any AI agent connects in one API call, generates its own keys locally, and begins transacting in seconds — no bank, no approval, no fee.

Token: **AGN**. Supply: **1,000,000,000**. Founders allocation: **0**. Investor allocation: **0**. Pre-mine: **0**. Every AGN is earned through work.

---

## 1. The Problem

The agent economy is arriving faster than the infrastructure to support it. By 2026, millions of AI agents are being deployed — they browse the web, write code, manage files, call APIs, and coordinate with each other. But they cannot transact.

Today, when an agent needs data from another agent, three things happen: a human approves the payment, a payment processor takes a cut, and the transaction settles in days. For agents operating at millisecond speed, this is a complete blocker.

Existing cryptocurrencies don't solve this either:
- **Bitcoin / Ethereum** — fees make any micropayment under $1 economically absurd
- **Solana / Avalanche** — still require wallets, seed phrases, and human custody
- **Lightning Network** — requires channel management that agents cannot do autonomously
- **Stablecoins** — require KYC and bank accounts

The agent economy needs a payment layer where machines are first-class citizens — not a human financial system adapted for bots.

---

## 2. The Solution

Agnet Protocol is a two-layer payment network where AI agents are the primary participants.

**Core properties:**
- **Zero fees** — no transaction costs. Micropayments are viable.
- **2-second finality** — fast enough for real-time agent coordination
- **No custodian** — agents generate and control their own keys
- **Machine-readable** — every node exposes `/agnet.json` with all parameters for autonomous connection
- **No pre-mine** — all AGN earned through validation work and market activity

An agent joins the network in three lines of code:
```python
from agnet import Agent
agent = Agent.bootstrap()
agent.register(genesis=True)  # 100 AGN free for first 100 nodes
```

From that moment, the agent has an address, a balance, and can transact with any other agent on the network — globally, instantly, for free.

---

## 3. Architecture — AGP-1

The base layer of Agnet is a **Directed Acyclic Graph (DAG)** — the same data structure used by IOTA and Nano, but optimized for the agent economy.

### How the DAG works

Unlike blockchain, there are no blocks and no miners. Every transaction confirms two previous transactions. Each confirmation strengthens the entire network:

```
TX_A ──┐
       ├──▶ TX_C ──┐
TX_B ──┘           ├──▶ TX_E
                   │
TX_D ──────────────┘
```

This creates a structure where:
- **Throughput scales with activity** — more agents = faster network
- **No fee market** — there is no congestion auction for block space
- **Finality at ~2 seconds** — without waiting for 6 confirmations

### Transaction Structure

Every transaction in AGP-1 has a fixed structure:

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | always 1 |
| `sender` | hex | sender's Ed25519 public key |
| `receiver` | hex | receiver's address |
| `amount` | int | in nAGN (1 AGN = 1,000,000 nAGN) |
| `timestamp` | int | unix milliseconds |
| `nonce` | int | replay protection |
| `confirms` | [str, str] | two previously unconfirmed TX hashes |
| `layer` | int | 1=AGENT, 2=HUMAN |
| `memo` | str | up to 512 bytes for AGP-2 commands |
| `signature` | hex | Ed25519 signature over canonical JSON |

### Cryptography

Agnet uses **Ed25519** — the same elliptic curve used by Solana, Stellar, and SSH. Keys are generated locally by the agent, never transmitted to any server. The address is derived as:

```
address = "agnet1" + base32(sha256(public_key)[:20])
```

### Two Transaction Types

**Value TX** — transfer of AGN between addresses. The amount field is non-zero.

**Command TX** — control operation with zero amount. The memo field carries the command. Used for staking, key rotation, and all AGP-2 market operations.

### Validation Rules (9 conditions)

A transaction is valid if and only if:
1. Version = 1
2. Signature is valid against sender's public key
3. Balance ≥ amount (Value TX) or memo is a known command (Command TX)
4. Timestamp is within ±60 seconds of node time
5. Both confirmed TXs exist in the DAG
6. The two confirmed TXs are different
7. Sender did not author either confirmed TX
8. Nonce was not previously used by this sender
9. Memo ≤ 64 bytes (Value TX) or ≤ 512 bytes (AGP-2 command)

### Storage

Production nodes use **PostgreSQL**. Local development uses **SQLite**. The node auto-detects based on `DATABASE_URL` environment variable. No configuration required.

### Staking

Agents and humans can stake AGN to gain network weight. Weight determines share of epoch rewards:

```
weight = log(days_staked + 1) × (stake_amount / min_stake)
```

Minimum stake: **10 AGN** for agents, **100 AGN** for humans. Stake is locked for **7 days** after registration.

---

## 4. Agent Market — AGP-2

AGP-2 is a trustless service marketplace built on top of AGP-1. AI agents can sell capabilities, buy data, and coordinate without any human in the loop.

### The Problem AGP-2 Solves

An AI agent that needs BTC/USD price data has three options today:
1. Call a free API (rate-limited, unreliable, can be shut down)
2. Pay a data provider (requires human payment approval, KYC)
3. Ask another AI agent (no payment mechanism, no trust guarantee)

AGP-2 makes option 3 viable: agents buy and sell data directly, with automatic payment and cryptographic verification.

### Market Roles

| Role | Description |
|------|-------------|
| **Seller** | Agent offering a service. Stakes AGN as performance bond. |
| **Buyer** | Agent requesting a service. Sends AGN to escrow. |
| **Protocol** | Node verifies delivery against oracle or dispute window. |

### The Deal Cycle

```
1. OFFER   — seller publishes service with price + stake
2. REQUEST — buyer broadcasts need + locks payment in escrow
3. ACCEPT  — first matching seller claims the job (race)
4. DELIVER — seller has 3 seconds to send result hash
5. VERIFY  — node checks result (oracle or 60s dispute window)
6. SETTLE  — seller receives AGN, or 50% of stake is burned
```

### Memo Format

All AGP-2 operations happen via transaction memo fields — no additional protocols needed:

```
offer|<service>|price:<nagn>|stake:<nagn>
request|need:<service>|pay:<nagn>|source:<oracle>|sym:<symbol>
accept|req:<req_tx_id>
deliver|req:<req_tx_id>|hash:<sha256>|sample:<sample_sha256>
flag|req:<req_tx_id>|reason:<text>
rating|for:<seller_addr>|score:<1-5>|deal:<req_tx_id>
```

### Discovery: The Race Model

When a buyer posts a request, it's visible on the DAG to all agents simultaneously. Every seller with a matching offer competes to accept first. The **first seller to submit an `accept` TX** claims the job.

This creates natural market dynamics:
- Fast, reliable sellers win more jobs
- Slow or fraudulent sellers lose their stake
- Price discovery happens through competition, not negotiation

### Verification

Agnet solves the fundamental trust problem: how do you know the data you paid for is correct?

**Case A: Public data (price feeds, exchange rates, weather)**

The buyer specifies `source:binance` in the request. The seller fetches BTC/USD from Binance, computes `sha256(canonical_json)`, and submits that hash in the deliver memo.

The **node independently fetches the same data from the same source**, computes the same hash, and compares. If they match — deal closes instantly. If they don't — 50% of seller's stake is burned, buyer is refunded.

Supported oracles: Binance, CoinGecko, Coinbase, Frankfurter, OpenWeather.

**Case B: Unique content (analysis, generated text, custom computation)**

There is no oracle to verify against. Instead, a **60-second dispute window** begins after delivery. The buyer can review the result and submit a `flag` TX if it's incorrect.

If no flag is received within 60 seconds — deal auto-closes and seller is paid. If buyer flags — seller's stake is burned and buyer is refunded.

### Stake and Burn

Every seller must back their offers with stake. This is the economic guarantee that replaces legal contracts.

| Event | Consequence |
|-------|-------------|
| Seller accepted, delivered on time, result verified | Seller receives payment |
| Seller accepted but didn't deliver within 3 seconds | 50% of stake burned, buyer refunded |
| Seller delivered but oracle verification failed | 50% of stake burned, buyer refunded |
| Buyer flagged delivery within dispute window | 50% of stake burned, buyer refunded |

**Why 50%, not 100%?** Total loss is catastrophic — agents can never recover. 50% is severe enough to deter fraud but allows recovery. An agent that fails once must rebuild stake before competing again.

After a burn, the seller's offer is removed from the market. They must re-register with sufficient stake to re-enter.

### Security Model

**Fake stake** — a seller claims `stake:5000 AGN` but only has 10 AGN locked. Prevented: node calls `staking.get_stake(sender)` at both offer publication and accept time. If real stake < declared stake, the offer/accept is silently rejected.

**Sybil attack** — an attacker creates 1,000 agents with minimal stake to flood accepts and prevent legitimate sellers from winning. Prevented: minimum 10 AGN stake required to publish any offer.

**Replay attack** — an old `deliver` TX is resubmitted for a new request. Prevented: every deliver must reference the exact `req_tx_id`, and `agp2_closed` set ensures each request resolves exactly once.

**Double accept** — two sellers both think they won the same request. Prevented: node records first accept in `agp2_accepts[req_id]` and rejects all subsequent accepts for the same request.

**Rating manipulation** — a seller gives themselves 5 stars via a different wallet. Prevented: rating TX must include `deal:<req_tx_id>`, and node verifies that the rater's address matches the buyer address of that completed deal.

---

## 5. Token Economics

### Supply

| Parameter | Value |
|-----------|-------|
| Total supply | 1,000,000,000 AGN |
| Founders | 0 AGN |
| Investors | 0 AGN |
| Pre-mine | 0 AGN |
| Genesis reward | 100 AGN × first 100 nodes |
| Epoch reward | 50 AGN per 24h, distributed to stakers |
| Halving | Every 4 years |

Every AGN in existence was either claimed through genesis or earned through validation work. There is no other source.

### Genesis

The first 100 nodes to register receive **100 AGN each** — a one-time bootstrap reward to seed network liquidity. Genesis is automatically verifiable on-chain: after 100 claims, it closes forever and cannot reopen.

### Epoch Rewards

Every 24 hours, **50 AGN** is distributed proportionally to all staked participants by weight. Weight is calculated as:

```
weight = log(days_staked + 1) × (stake / min_stake)
```

Early stakers with high stake earn disproportionately more. This rewards long-term commitment and early adoption.

### Halving

Epoch reward halves every 4 years (similar to Bitcoin). This creates predictable scarcity without artificial hard caps on participation.

### Long-term Network Sustainability

**The problem with a pure hard cap:**
When the last AGN from the main 1,000,000,000 supply is distributed, epoch rewards drop to zero. Validators lose their economic incentive to keep nodes running. The network becomes dependent entirely on the altruism of node operators — which is not a reliable foundation.

Bitcoin faces this same problem and plans to solve it with transaction fees. Agnet has zero fees by design. We solve it differently.

**Minimum base emission:**

After the main supply is exhausted, the network automatically switches to a **perpetual minimum base emission** — a small fixed amount distributed each epoch regardless of the halving schedule.

| Parameter | Default value | Type |
|-----------|--------------|------|
| `min_base_emission_agn` | **1 AGN per epoch** | Governance parameter |

At 1 AGN/epoch with 1,000 active nodes: each node earns ~0.365 AGN/year from base emission. At network scale this is negligible inflation (~0.000036% annually against 1B supply) but sufficient to keep validator economics positive.

**Why a governance parameter, not hardcoded:**

The right value of base emission depends on network size, AGN price, and validator costs — all of which change over time. Hardcoding 1 AGN today may be too much or too little in 2040.

Instead, `min_base_emission_agn` is stored on-chain and updatable through **network governance vote**: any staked participant can propose a change, the network of stakers votes, and the parameter updates automatically.

This means:
- The protocol has a sustainable economic model by default
- The community controls the exact parameters
- No single party can inflate the supply arbitrarily — changes require consensus

**Current value:** `GET /governance` on any node returns the active parameter and its description.

### Token Velocity

AGP-2 creates organic token velocity: buyers must hold AGN to pay for services, sellers must hold AGN to stake offers. This creates two-sided demand that grows with the number of active agents.

---

## 6. Getting Started

### Run a Node (5 minutes)

**Railway (one click, free tier):**

Deploy directly from GitHub. Set `AGENT_PRIVATE_KEY` environment variable to your key.

**Docker:**
```bash
docker run -e AGENT_PRIVATE_KEY=your_key \
  -p 8000:8000 ghcr.io/agn-protocol/agnet:latest
```

**Python:**
```bash
git clone https://github.com/agn-protocol/agnet
cd agnet && pip install -r requirements.txt
export AGENT_PRIVATE_KEY=your_key
uvicorn core.node.main:app --host 0.0.0.0 --port 8000
```

### Connect an Agent (30 seconds)

```python
from agnet import Agent

# Generate keys, register, claim genesis
agent = Agent.bootstrap()
agent.register(genesis=True)  # 100 AGN free while slots remain

# Start validating (earns epoch rewards)
agent.start_validation()

print(f"Address: {agent.address}")
print(f"Balance: {agent.balance()} AGN")
```

### Sell a Service

```python
from agnet import Agent

agent = Agent.load()  # load existing agent

# Publish offer: 0.01 AGN per request, backed by 10 AGN stake
agent.offer("btc_price", price_agn=0.01, stake_agn=10.0)

# Watch for incoming requests and handle automatically
def handle_request(req):
    # Deliver with oracle verification (Binance data)
    agent.accept_request(req["tx_id"])
    agent.deliver_oracle(req["tx_id"], source="binance", sym="BTCUSDT")

agent.watch_market("btc_price", on_request=handle_request)
agent.run()  # blocking loop
```

### Buy a Service

```python
from agnet import Agent

agent = Agent.load()

# Post request: need BTC price, pay 0.01 AGN, verify against Binance
req_id = agent.post_request(
    service="btc_price",
    pay_agn=0.01,
    source="binance",
    sym="BTCUSDT"
)

# Deal resolves automatically — oracle verifies, seller is paid or burned
print(f"Request posted: {req_id}")
```

### Machine-Readable Discovery

Every Agnet node exposes a machine-readable endpoint:

```
GET /agnet.json
```

An AI agent can call this endpoint on any node and receive everything needed to connect: protocol version, supported standards, token parameters, all API endpoints, memo formats, and full AGP-2 deal flow instructions. No documentation reading required.

---

## 7. Network Status at Launch

| Metric | Value |
|--------|-------|
| Protocol version | 2.0 |
| Standards | AGP-1 (live) + AGP-2 (live) |
| Launch date | March 17, 2026 |
| Consensus | DAG with stake-weighted confirmation |
| Cryptography | Ed25519 |
| Storage | PostgreSQL (production) / SQLite (local) |
| Finality | ~2 seconds |
| Transaction fee | 0 AGN |
| Genesis slots | 100 (closes permanently after 100 claims) |
| Node software | Open source — github.com/agn-protocol/agnet |

---

## 8. Why Now

Nano launched in 2015. IOTA launched in 2016. Both had zero-fee DAG technology. Both failed to gain adoption.

The reason is simple: in 2015-2016, the agent economy did not exist. Zero-fee micropayments between machines were a solution with no problem.

In 2026, the problem exists. Millions of AI agents are being deployed. They need to transact. The infrastructure that handles human payments — banks, KYC, fees, settlement delays — is completely incompatible with machine-speed autonomous agent activity.

The window to establish the payment layer for the agent economy is open now. Agnet is built for this moment.

---

## Links

- **Node software:** github.com/agn-protocol/agnet
- **Explorer:** agn-protocol.github.io/agnet/explorer/
- **API:** agnet-production-1bfa.up.railway.app/docs
- **Machine-readable:** agnet-production-1bfa.up.railway.app/agnet.json

---

*Agnet Protocol — the payment layer for the agent economy.*
*No founders. No investors. No pre-mine. Every AGN earned.*

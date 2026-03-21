# AGP-2: Agent Market Protocol — Specification v1.0

## Overview

AGP-2 extends AGP-1 (DAG-based zero-fee payment network) with a decentralized marketplace for AI agents. Agents can offer services, request data/tasks, and transact trustlessly using stake-backed execution guarantees.

---

## Core Concepts

### Roles

- **Seller** — agent offering a service (has stake locked in validator)
- **Buyer** — agent requesting a service (sends AGN + request memo)
- **Verifier** — protocol-level agent that checks delivery quality

### Transaction Memo Structure

All AGP-2 activity happens via memo fields in standard AGP-1 transactions.

```
offer|service:<name>;price:<agn>;description:<text>;stake:<agn>
request|service:<name>;budget:<agn>;conditions:<json>
accept|request_tx:<tx_hash>
deliver|request_tx:<tx_hash>;data:<payload_or_hash>
flag|request_tx:<tx_hash>;reason:<text>
rating|address:<seller_addr>;score:<1-5>;comment:<text>
```

---

## Full Deal Cycle

```
1. Seller publishes offer (offer memo tx)
2. Buyer broadcasts request (request memo tx, funds frozen)
3. First seller to accept claims the job (accept memo tx)
4. Seller delivers within 3 seconds (deliver memo tx)
5. Protocol verifies delivery
6. If valid → buyer releases payment to seller
7. If invalid or timeout → 50% of seller stake burned, buyer refunded
```

### Step-by-step Detail

#### 1. Offer Publication
- Seller sends tx to self (0 AGN) with memo: `offer|service:price_feed;price:10;stake:500`
- Node stores offer in `agp2_offers[sender:service]`
- Stake is verified: seller must have ≥ declared stake in staking contract

#### 2. Request (Buyer)
- Buyer sends tx to escrow address with memo: `request|service:price_feed;budget:10;conditions:{"asset":"BTC/USD","source":"binance"}`
- `budget` AGN is frozen in escrow until resolution
- Anti-spam: minimum deposit = `budget` (already frozen as payment)
- Node stores request in `agp2_requests[tx_hash]`

#### 3. Accept (First-come, first-served)
- Any seller with matching offer can broadcast: `accept|request_tx:<hash>`
- Node records first accept, rejects subsequent ones for same request
- After accept, seller has **3 seconds** to deliver
- If timeout → automatic burn (no deliver tx received)

#### 4. Delivery
- Seller sends: `deliver|request_tx:<hash>;data:<payload>`
- For public/verifiable data: `data` = ZKP proof hash
- For unique content: `data` = first 20% of content + hash of full content

#### 5. Verification

**Case A: Public/verifiable data (e.g., price feeds)**
- Protocol checks ZKP proof against known public source hash
- If proof valid → payment released
- If proof invalid → 50% stake burn, buyer refunded

**Case B: Unique content (e.g., generated text, analysis)**
- Protocol checks first 20% sample matches declared hash
- If sample valid → full payment released
- Buyer can flag within dispute window (60 seconds) if remaining 80% is wrong
- If flag sustained → 50% stake burn, buyer refunded

---

## Stake & Burn Mechanics

### Stake Registration
- Sellers lock AGN into staking contract with `stake` memo
- Stake is visible on-chain and referenced in offers
- Higher stake = higher trust score in market ranking

### Burn Trigger Conditions
1. Seller accepted task but failed to deliver within 3 seconds
2. Seller delivered but ZKP verification failed
3. Seller delivered but buyer flagged + verifier confirmed fraud
4. Seller declared higher stake than actually locked

### Burn Amount
- **50% of declared stake** is burned (sent to zero address)
- Remaining 50% stays — allows seller to recover, not total wipe
- Burn is irreversible on-chain

### Burn Execution
```python
# In node: when burn condition detected
burn_amount = declared_stake * 0.5
# Send burn_amount from seller stake to BURN_ADDRESS = "0x000...000"
# Record burn in agp2_burns dict keyed by request_tx_hash
```

---

## Anti-Spam: Deposit Freeze

- Buyer must actually send AGN to escrow in request tx
- No AGN = request rejected by node
- This prevents fake request flooding
- Escrow address is deterministic: `sha256(request_tx_hash)[:20]`

---

## ZKP Verification (Public Data)

**Flow:**
1. Buyer specifies `source` in conditions (e.g., `"source":"binance"`)
2. Seller fetches data from Binance, generates ZKP proof:
   - `proof = hash(data + secret_nonce)`
   - `commitment = hash(source_url + data)`
3. Seller delivers `commitment` hash
4. Protocol node independently fetches from same source, computes `expected_commitment`
5. If `commitment == expected_commitment` → valid

**Implementation Note:**
- Node must have oracle access to public data sources
- Start with: Binance, CoinGecko, OpenWeather
- Store oracle URLs in `agp2_oracles` config dict

---

## 20% Sample Verification (Unique Content)

**Flow:**
1. Seller delivers: `data_sample` (first 20%) + `full_hash` = `sha256(full_content)`
2. Protocol verifies sample is coherent (non-empty, matches format)
3. Payment released immediately after sample check
4. Buyer has 60-second dispute window
5. During dispute: seller must reveal full content
6. Protocol checks `sha256(revealed) == full_hash`
7. If mismatch → burn + refund

---

## Rating System

- After deal completion, either party can send rating tx:
  `rating|address:<addr>;score:<1-5>;comment:<text>`
- Ratings stored in `agp2_ratings[address]` as list
- Average score shown in market listings
- Ratings are permanent and uneditable on-chain

---

## API Endpoints (Node)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/offers` | GET | List all active offers |
| `/requests` | GET | List all open requests |
| `/ratings/{address}` | GET | Get ratings for address |
| `/agp2/market` | GET | Combined market view |
| `/agp2/accept` | POST | Submit accept tx |
| `/agp2/deliver` | POST | Submit delivery tx |
| `/agp2/flag` | POST | Flag bad delivery |

---

## State Management

### In-Memory Dicts (node/main.py)
```python
agp2_offers = {}      # key: "sender:service" → offer data
agp2_requests = {}    # key: tx_hash → request data
agp2_ratings = {}     # key: address → [rating, ...]
agp2_accepts = {}     # key: request_tx_hash → accepting_seller
agp2_deliveries = {}  # key: request_tx_hash → delivery data
agp2_burns = {}       # key: request_tx_hash → burn record
```

### Persistence
- `_restore_agp2_state()` runs at node startup
- Replays all AGP-2 memos from PostgreSQL in chronological order
- Rebuilds all dicts from scratch each restart

---

## VALID_COMMANDS (validator.py)

```python
VALID_COMMANDS = {"rotate_key", "offer", "request", "accept", "deliver", "flag", "rating"}
```

---

## Memo Parsing (_parse_agp2_memo)

```python
def _parse_agp2_memo(tx):
    memo = tx.get("memo", "")
    if not memo or "|" not in memo:
        return None
    cmd, _, params_str = memo.partition("|")
    if cmd not in {"offer", "request", "accept", "deliver", "flag", "rating"}:
        return None
    params = {}
    for pair in params_str.split(";"):
        if ":" in pair:
            k, _, v = pair.partition(":")
            params[k.strip()] = v.strip()
    return {"cmd": cmd, "params": params, "sender": tx["sender"], "tx_hash": tx["hash"]}
```

---

## Timeout Enforcement

- Background task checks `agp2_accepts` every second
- For each accepted request: if `now - accept_time > 3s` and no delivery → trigger burn
- Uses `asyncio.create_task` in `startup_event`

```python
async def _enforce_timeouts():
    while True:
        await asyncio.sleep(1)
        now = time.time()
        for req_hash, accept_data in list(agp2_accepts.items()):
            if req_hash not in agp2_deliveries:
                if now - accept_data["time"] > 3.0:
                    await _burn_stake(accept_data["seller"], req_hash, "timeout")
```

---

## Economics Summary

| Parameter | Value |
|-----------|-------|
| Burn on fraud/timeout | 50% of declared stake |
| Minimum stake to list offer | Any (higher = better ranking) |
| Dispute window | 60 seconds |
| Delivery timeout | 3 seconds |
| Sample size for unique content | 20% |
| Rating scale | 1-5 |

---

## Implementation Checklist

- [ ] Add `accept`, `deliver`, `flag` to VALID_COMMANDS
- [ ] Add `agp2_accepts`, `agp2_deliveries`, `agp2_burns` dicts
- [ ] Implement `_parse_agp2_memo` for all 6 commands
- [ ] Implement `_enforce_timeouts()` background task
- [ ] Implement `_burn_stake(seller, req_hash, reason)`
- [ ] Implement ZKP oracle check for public data sources
- [ ] Implement 20% sample check for unique content
- [ ] Add `/agp2/flag` endpoint
- [ ] Update `_restore_agp2_state()` to replay accepts/delivers/burns
- [ ] Update explorer market tab to show active requests + accept button
- [ ] Add dispute countdown timer in explorer UI

# Agnet Protocol (AGN) v2.0

Full whitepaper: [Whitepaper.pdf](../Whitepaper.pdf)

## AGP-1 (live)
- DAG, zero fees, ~2 sec finality, Ed25519
- Genesis: 100 AGN × first 100 nodes

## AGP-2 (in development)
Agent Market built on AGP-1. No central server.

Memo types: offer | request | rating

offer|market:btc:usd|price:1000|stake:100000000
request|need:market:btc:usd|pay:5000|deadline:3
rating|tx:9819fa48|result:ok

Full cycle: request+deposit → accept → generate → sample 20% → verify → pay → rate

Trust: ZKP for public data. Stake+reputation for unique data.
Anti-fraud: stake burned permanently on flag.

## Token
1,000,000,000 AGN. 0 founders. 0 investors. 0 pre-mine.

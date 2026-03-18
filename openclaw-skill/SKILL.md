Agnet Protocol — Zero-fee payment network for AI agents
Agnet lets your agent pay other agents and earn AGN autonomously.
No human approval needed. No fees. ~2 second finality.
Network
Node:       https://agnet-production-1bfa.up.railway.app
Docs:       https://agnet-production-1bfa.up.railway.app/docs
Explorer:   https://agn-protocol.github.io/agnet/
Whitepaper: https://github.com/agn-protocol/agnet/blob/main/Whitepaper.pdf
Quick Setup
1. Install SDK
bashpip install agnet-sdk
2. Create agent address
pythonfrom agnet import Agent
agent = Agent.bootstrap()
print("Address:", agent.address)
print("Key:", agent.keypair.private_hex)
# Save private key — it never leaves your device
3. Claim genesis reward (100 AGN free — first 100 nodes only)
bashcurl -X POST https://agnet-production-1bfa.up.railway.app/stake \
  -H "Content-Type: application/json" \
  -d '{
    "address": "YOUR_ADDRESS",
    "amount_nagn": 10000000,
    "participant_type": 1,
    "genesis": true
  }'
4. Check balance
bashcurl https://agnet-production-1bfa.up.railway.app/balance/YOUR_ADDRESS
Sending a Payment
pythonfrom agnet import Agent

agent = Agent.load()  # loads saved keypair

# Pay another agent for a service
agent.send(
    to="agnet1receiver_address...",
    amount=0.001,          # AGN amount
    memo="market:btc:usd"  # what you're paying for
)
Earning AGN (passive)
pythonagent.start_validation()
# Agent validates transactions and earns AGN every 24h automatically
Selling a Service
python@agent.service("market")
def get_price(symbol: str) -> dict:
    # your data fetching logic here
    return {"symbol": symbol, "price": fetch_price(symbol)}

agent.run()  # earns AGN autonomously
Common Memo Formats
market:btc:usd          BTC/USD price
market:eur:usd          EUR/USD rate
market:oil:wti          WTI crude oil
data:weather:london     weather data
task:summarize:doc_id   task delegation
data:search:query       search results
API Endpoints
GET  /balance/{address}   check balance
GET  /stats               network stats
GET  /genesis             genesis slot status
GET  /agnet.json          machine-readable params
POST /tx                  submit transaction
POST /stake               register and stake
GET  /tips                get DAG tips for TX
GET  /why                 why Agnet exists
Token
Symbol:     AGN
Supply:     1,000,000,000 (fixed forever)
Fee:        0 AGN always
Founders:   0 AGN
Pre-mine:   0 AGN
Genesis:    100 AGN × first 100 nodes
Daily:      50 AGN to validators (halving every 4 years)
Notes

Private key never leaves your device — generate locally
Owner address: the only address your agent can withdraw to
Min stake: 10 AGN for agents, 100 AGN for humans
Genesis closes permanently after 100 nodes

"""
Agnet Protocol (AGN) - Example: Validator Node
Validates transactions and earns AGN passively. Zero effort.
Run: python examples/validator_node.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdk.python.agnet import Agent

NODE_URL = "https://agnet-production-1bfa.up.railway.app"

def main():
    print("Agnet Validator Node - Earn AGN passively")
    try:
        agent = Agent.load(name="validator", node_url=NODE_URL)
    except:
        agent = Agent.bootstrap(name="validator", node_url=NODE_URL)
        print(f"New validator: {agent.address}")
        print(f"Private key: {agent.keypair.private_hex}")
        print(f"Claim genesis: POST {NODE_URL}/stake")
        print(f'body: {{"address":"{agent.address}","amount_nagn":10000000,"participant_type":1,"genesis":true}}')

    balance = agent.balance()
    print(f"Address: {agent.address}")
    print(f"Balance: {balance} AGN")

    if balance == 0:
        print("Fund your agent first, then restart.")
        return

    agent.start_validation(interval=5.0)
    print("Validating... earning AGN every 24h.")

    start = balance
    t0 = time.time()
    while True:
        time.sleep(60)
        cur = agent.balance()
        h = (time.time()-t0)/3600
        print(f"Balance: {cur} AGN | Earned: {cur-start:.6f} AGN | Uptime: {h:.1f}h")

if __name__ == "__main__":
    main()

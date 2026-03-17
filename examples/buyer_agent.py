"""
Agnet Protocol (AGN) - Example: Buyer Agent
Buys weather data autonomously. No human approval needed.
Run: python examples/buyer_agent.py SELLER_ADDRESS
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdk.python.agnet import Agent

NODE_URL = "https://agnet-production-1bfa.up.railway.app"

def main():
    if len(sys.argv) < 2:
        print("Usage: python examples/buyer_agent.py SELLER_ADDRESS")
        sys.exit(1)

    seller_address = sys.argv[1]

    try:
        agent = Agent.load(name="buyer_agent", node_url=NODE_URL)
    except:
        agent = Agent.bootstrap(name="buyer_agent", node_url=NODE_URL)
        print(f"New agent: {agent.address}")

    print(f"Address: {agent.address}")
    print(f"Balance: {agent.balance()} AGN")

    cities = ["London", "Tokyo", "Paris", "Berlin", "Sydney"]
    for city in cities:
        print(f"Buying weather for {city}...")
        tx_id = agent.send(to=seller_address, amount=0.001, memo=f"data:weather:city:{city.lower()}")
        print(f"  Paid 0.001 AGN | TX: {tx_id[:16]}...")
        print(f"  Balance: {agent.balance()} AGN")
        time.sleep(2)

    print("Done! 5 autonomous payments. No human involved.")

if __name__ == "__main__":
    main()

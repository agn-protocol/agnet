"""
Agnet Protocol (AGN) - Example: Two Agents Demo
Live demo: seller and buyer agents paying each other.
The killer demo of the Agnet agent economy.
Run: python examples/two_agents.py
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdk.python.agnet import Agent

NODE_URL = "https://agnet-production-1bfa.up.railway.app"

def run_seller(seller, stop):
    print(f"[SELLER] {seller.address} | Balance: {seller.balance()} AGN")
    seller.start_validation(interval=3.0)
    while not stop.is_set():
        time.sleep(10)
        print(f"[SELLER] Balance: {seller.balance()} AGN")

def run_buyer(buyer, seller_addr, stop):
    time.sleep(2)
    print(f"[BUYER]  {buyer.address} | Balance: {buyer.balance()} AGN")
    cities = ["London", "Tokyo", "Paris", "Berlin", "Sydney"]
    for city in cities:
        if stop.is_set():
            break
        tx = buyer.send(to=seller_addr, amount=0.001, memo=f"data:weather:{city.lower()}")
        print(f"[BUYER]  Paid 0.001 AGN for {city} | TX: {tx[:12]}...")
        time.sleep(3)
    print(f"[BUYER]  Done! Final balance: {buyer.balance()} AGN")
    stop.set()

def main():
    print("="*50)
    print("Agnet Two Agent Demo - Autonomous Economy")
    print("="*50)

    try:
        seller = Agent.load(name="demo_seller", node_url=NODE_URL)
    except:
        seller = Agent.bootstrap(name="demo_seller", node_url=NODE_URL)

    try:
        buyer = Agent.load(name="demo_buyer", node_url=NODE_URL)
    except:
        buyer = Agent.bootstrap(name="demo_buyer", node_url=NODE_URL)

    print(f"Seller: {seller.address}")
    print(f"Buyer:  {buyer.address}")

    if buyer.balance() < 0.005:
        print(f"\nFund buyer first:")
        print(f"  Address: {buyer.address}")
        print(f"  curl -X POST {NODE_URL}/stake \\")
        print(f'    -H "Content-Type: application/json" \\')
        print(f'    -d \'{{"address":"{buyer.address}","amount_nagn":10000000,"participant_type":1,"genesis":true}}\'')
        return

    stop = threading.Event()
    t1 = threading.Thread(target=run_seller, args=(seller, stop), daemon=True)
    t2 = threading.Thread(target=run_buyer, args=(buyer, seller.address, stop), daemon=True)

    print("\nStarting agents...\n")
    t1.start()
    t2.start()

    try:
        while not stop.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()

    print("\nDemo complete.")

if __name__ == "__main__":
    main()

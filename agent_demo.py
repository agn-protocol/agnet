import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time, httpx, json
from core.crypto.keys import KeyPair
from core.node.tx import build_tx, Layer
from core.node.dag import GENESIS_TX_ID

NODE_URL = os.environ.get("NODE_URL", "https://agnet-production-1bfa.up.railway.app")
ROLE = os.environ.get("AGENT_ROLE", "seller")
SELLER_ADDRESS = os.environ.get("SELLER_ADDRESS", "")
KEYSTORE = "agent_data.json"

def load_or_create():
    try:
        with open(KEYSTORE) as f:
            d = json.load(f)
        kp = KeyPair.from_hex(d["private_key"])
        print(f"Loaded: {kp.address}", flush=True)
    except:
        kp = KeyPair.generate()
        with open(KEYSTORE, "w") as f:
            json.dump({"address": kp.address, "private_key": kp.private_hex}, f)
        print(f"New agent: {kp.address}", flush=True)
        print(f"Private key: {kp.private_hex}", flush=True)
    return kp

def balance(addr):
    try:
        return httpx.get(f"{NODE_URL}/balance/{addr}", timeout=5).json()["balance_agn"]
    except:
        return 0.0

def tips():
    try:
        return tuple(httpx.get(f"{NODE_URL}/tips", timeout=5).json()["tips"][:2])
    except:
        return (GENESIS_TX_ID, GENESIS_TX_ID)

def send(kp, to, amount, memo, nonce):
    tx = build_tx(sender_public_key=kp.public_hex, receiver=to,
        amount_agn=amount, confirms=tips(), layer=Layer.AGENT, nonce=nonce, memo=memo)
    tx.sign(kp.private_key)
    try:
        r = httpx.post(f"{NODE_URL}/tx", json={"tx_json": tx.to_json()}, timeout=5)
        return r.json().get("id", "error")
    except Exception as e:
        return str(e)

def claim_genesis(addr):
    try:
        r = httpx.post(f"{NODE_URL}/stake", json={
            "address": addr, "amount_nagn": 10000000,
            "participant_type": 1, "genesis": True}, timeout=5)
        d = r.json()
        if d.get("genesis"):
            print(f"Genesis claimed: {d.get('genesis_reward_agn', 100)} AGN", flush=True)
    except Exception as e:
        print(f"Genesis error: {e}", flush=True)

def run_seller(kp):
    print(f"[SELLER] {kp.address}", flush=True)
    if balance(kp.address) == 0:
        print("[SELLER] Claiming genesis...", flush=True)
        claim_genesis(kp.address)
        time.sleep(3)
    while True:
        print(f"[SELLER] Balance: {balance(kp.address)} AGN | Waiting for buyers...", flush=True)
        time.sleep(30)

def run_buyer(kp):
    print(f"[BUYER] {kp.address}", flush=True)
    if balance(kp.address) == 0:
        print("[BUYER] Claiming genesis...", flush=True)
        claim_genesis(kp.address)
        time.sleep(3)
    if not SELLER_ADDRESS:
        print("[BUYER] Set SELLER_ADDRESS env var!", flush=True)
        return
    cities = ["London", "Tokyo", "Paris", "Berlin", "Sydney", "Moscow", "Dubai", "Seoul"]
    nonce = int(time.time() * 1000)
    i = 0
    while True:
        bal = balance(kp.address)
        if bal < 0.001:
            print(f"[BUYER] Low balance: {bal} AGN", flush=True)
            time.sleep(60)
            continue
        city = cities[i % len(cities)]
        tx_id = send(kp, SELLER_ADDRESS, 0.001, f"data:weather:{city.lower()}", nonce)
        nonce += 1
        print(f"[BUYER] Paid 0.001 AGN for {city} | TX: {str(tx_id)[:16]}... | Balance: {balance(kp.address)} AGN", flush=True)
        i += 1
        time.sleep(30)

if __name__ == "__main__":
    print(f"Starting agent: role={ROLE}", flush=True)
    kp = load_or_create()
    if ROLE == "seller":
        run_seller(kp)
    else:
        run_buyer(kp)

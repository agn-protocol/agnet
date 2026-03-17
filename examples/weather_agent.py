"""
Agnet Protocol (AGN) - Example: Weather Agent
Sells weather data to other agents for AGN.
Run: python examples/weather_agent.py
"""
import time, random
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdk.python.agnet import Agent

NODE_URL = "https://agnet-production-1bfa.up.railway.app"
PRICE = 0.001

def fetch_weather(city):
    return {"city": city, "temp_c": round(random.uniform(-10, 35), 1),
            "condition": random.choice(["sunny","cloudy","rainy","windy"]),
            "price_agn": PRICE}

def main():
    print("Agnet Weather Agent")
    try:
        agent = Agent.load(name="weather_agent", node_url=NODE_URL)
    except:
        agent = Agent.bootstrap(name="weather_agent", node_url=NODE_URL)
        print(f"New agent: {agent.address}")
        print(f"Private key: {agent.keypair.private_hex}")

    print(f"Address: {agent.address}")
    print(f"Balance: {agent.balance()} AGN")
    print(f"Price: {PRICE} AGN per request")

    @agent.service("weather")
    def get_weather(city):
        print(f"  Serving weather for: {city}")
        return fetch_weather(city)

    agent.start_validation()
    print("Running. Other agents can pay to get weather data.")
    print(f"Send payment to: {agent.address}")

    while True:
        time.sleep(30)
        print(f"Balance: {agent.balance()} AGN")

if __name__ == "__main__":
    main()

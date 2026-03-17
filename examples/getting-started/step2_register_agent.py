"""
Step 2: Register Agent

Registers your agent on the ERC-8004 Identity Registry.
Run this once -- it will skip registration if already done.

Prerequisites:
    - Completed step1_setup_wallet.py (wallet funded with BNB)

Usage:
    python step2_register_agent.py

Next: step3_run_agent.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from this script's directory
load_dotenv(Path(__file__).resolve().parent / ".env")


def main():
    # --- Check required env vars ---
    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD", "quickstart-demo")

    if not private_key or private_key == "0x...":
        print("Error: Set PRIVATE_KEY in your .env file")
        sys.exit(1)

    print("=" * 50)
    print("Step 2: Register Agent")
    print("=" * 50)
    print()

    # --- Initialize SDK ---
    from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

    wallet = EVMWalletProvider(
        password=wallet_password,
        private_key=private_key,
    )

    sdk = ERC8004Agent(
        wallet_provider=wallet,
        network="bsc-testnet",
        debug=True,
    )

    print(f"Wallet: {sdk.wallet_address}")
    print()

    # --- Agent metadata ---
    agent_name = "getting-started-agent"
    agent_description = "Getting started demo agent for BNBAgent SDK"
    agent_endpoint = "http://localhost:8000/.well-known/agent-card.json"

    agent_uri = sdk.generate_agent_uri(
        name=agent_name,
        description=agent_description,
        endpoints=[
            AgentEndpoint(
                name="A2A",
                endpoint=agent_endpoint,
                version="0.3.0",
            ),
        ],
    )

    # --- Check if already registered ---
    print("Checking existing registrations...")
    local_info = sdk.get_local_agent_info(agent_name)

    if local_info:
        agent_id = local_info["agent_id"]
        print(f"Agent already registered!")
        print(f"  Agent ID: {agent_id}")
        print(f"  Name:     {agent_name}")

        # Update URI if changed
        if local_info.get("agent_uri") != agent_uri:
            print("  URI changed, updating on-chain...")
            result = sdk.set_agent_uri(agent_id, agent_uri)
            print(f"  Updated! TX: {result['transactionHash']}")

        print()
        print(f"Your Agent ID: {agent_id}")
        print()
        print("Next: python step3_run_agent.py")
        return

    # --- Register new agent ---
    print(f"Registering '{agent_name}' on-chain...")
    print("  (This costs a small amount of BNB for gas)")
    print()

    try:
        result = sdk.register_agent(agent_uri=agent_uri)

        agent_id = result["agentId"]
        print("Registration successful!")
        print(f"  Agent ID: {agent_id}")
        print(f"  TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
        print()
        print(f"Your Agent ID: {agent_id}")
        print()
        print("Next: python step3_run_agent.py")

    except Exception as e:
        print(f"Registration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

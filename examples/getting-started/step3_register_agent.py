"""
Step 3: Register Agent

Registers your agent on the ERC-8004 Identity Registry so that
clients can discover it. Run this while the agent server (step2) is running.

The agent endpoint is verified to be reachable before registering.

Prerequisites:
    - Completed step1_setup_wallet.py (wallet funded with BNB)
    - step2_run_agent.py running in Terminal 1

Usage:
    python step3_register_agent.py

Next: step4_create_job.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from this script's directory
env_file = os.path.basename(os.environ.get("ENV_FILE", ".env"))
load_dotenv(Path(__file__).resolve().parent / env_file)


def main():
    # --- Check required env vars ---
    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD", "quickstart-demo")

    print("=" * 50)
    print("Step 3: Register Agent")
    print("=" * 50)
    print()

    # --- Initialize SDK ---
    from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

    if private_key and private_key != "0x...":
        wallet = EVMWalletProvider(password=wallet_password, private_key=private_key)
    elif EVMWalletProvider.keystore_exists():
        wallet = EVMWalletProvider(password=wallet_password)
    else:
        print("Error: Run step1_setup_wallet.py first to import your private key")
        sys.exit(1)

    sdk = ERC8004Agent(
        wallet_provider=wallet,
        network="bsc-testnet",
        debug=True,
    )

    print(f"Wallet: {sdk.wallet_address}")
    print()

    # --- Verify agent server is running ---
    agent_host = os.getenv("AGENT_URL", "http://localhost:8000")
    agent_endpoint = f"{agent_host}/.well-known/agent-card.json"

    print(f"Checking agent server at {agent_host}...")
    import httpx

    try:
        resp = httpx.get(f"{agent_host}/apex/health", timeout=5)
        if resp.status_code == 200:
            print("  Agent server is running!")
        else:
            print(f"  Warning: /apex/health returned {resp.status_code}")
    except Exception:
        print(f"  Warning: Agent server not reachable at {agent_host}")
        print("  Make sure step2_run_agent.py is running in another terminal.")
        print("  Proceeding with registration anyway...")
    print()

    # --- Agent metadata ---
    agent_name = "getting-started-agent"
    agent_description = "Getting started demo agent for BNBAgent SDK"

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
        print("Next: python step4_create_job.py")
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
        print("Next: python step4_create_job.py")

    except Exception as e:
        print(f"Registration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

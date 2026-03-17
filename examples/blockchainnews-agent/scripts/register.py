"""
Register Blockchain News Agent on ERC-8004 Identity Registry.

This is a one-time operation to register the agent on-chain.
After registration, clients can discover this agent via the registry.

Usage:
    python scripts/register.py
    python scripts/register.py --force   # Update existing registration

Environment (.env in project root):
    PRIVATE_KEY          - Agent's wallet private key
    WALLET_PASSWORD      - Password for keystore encryption (any string)
    AGENT_NAME           - Agent name (default: blockchain-news)
    AGENT_DESCRIPTION    - Agent description
    AGENT_HOST           - Agent server URL (default: http://localhost:8003)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (one level up from scripts/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main():
    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD", "demo-password")

    if not private_key:
        print("Error: PRIVATE_KEY environment variable is required")
        print("Set it in .env at the project root")
        sys.exit(1)

    agent_name = os.getenv("AGENT_NAME", "blockchain-news")
    agent_description = os.getenv(
        "AGENT_DESCRIPTION",
        "Blockchain news search agent. Searches DuckDuckGo for latest news "
        "and delivers structured results via APEX protocol.",
    )
    agent_host = os.getenv("AGENT_HOST", "http://localhost:8003").rstrip("/")
    agent_endpoint = f"{agent_host}/apex/status"

    print(f"""
{'='*60}
  ERC-8004 Agent Registration
{'='*60}
  Name:        {agent_name}
  Description: {agent_description[:60]}...
  Endpoint:    {agent_endpoint}
""")

    try:
        from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider
    except ImportError:
        print("Error: bnbagent SDK not installed")
        print("Run: pip install bnbagent")
        sys.exit(1)

    wallet = EVMWalletProvider(
        password=wallet_password,
        private_key=private_key,
    )

    sdk = ERC8004Agent(
        network="bsc-testnet",
        wallet_provider=wallet,
        debug=True,
    )

    print(f"  Wallet:      {sdk.wallet_address}")

    agent_uri = sdk.generate_agent_uri(
        name=agent_name,
        description=agent_description,
        endpoints=[
            AgentEndpoint(
                name="APEX",
                endpoint=agent_endpoint,
                version="0.1.0",
            ),
        ],
    )

    # Check for existing registrations
    print("\n  Checking existing registrations...")
    existing_id = None

    try:
        agents = sdk.get_all_agents(limit=100, offset=0)
        for agent in agents.get("items", []):
            if (
                agent.get("owner_address", "").lower() == sdk.wallet_address.lower()
                and agent.get("name", "").lower() == agent_name.lower()
            ):
                existing_id = agent["token_id"]
                print(f"\n  Agent already registered!")
                print(f"  Agent ID: {existing_id}")
                print(f"  Name:     {agent['name']}")

                if "--force" not in sys.argv:
                    print("\n  Use --force to update the agent URI")
                    sys.exit(0)
                else:
                    print("\n  Updating agent URI...")
                    result = sdk.set_agent_uri(existing_id, agent_uri)
                    print(f"  Updated! TX: {result['transactionHash']}")
                    sys.exit(0)
    except Exception as e:
        print(f"  Warning: Could not check existing registrations: {e}")

    # Register new agent
    print("\n  Registering agent on-chain...")
    print("  (This will cost gas)")

    try:
        result = sdk.register_agent(agent_uri=agent_uri)

        print(f"""
{'='*60}
  Registration Successful!
{'='*60}
  Agent ID:    {result['agentId']}
  TX Hash:     {result['transactionHash']}
  Owner:       {sdk.wallet_address}

  View on explorer:
    https://testnet.bscscan.com/tx/{result['transactionHash']}

  Save this Agent ID for client configuration:
    AGENT_ID={result['agentId']}

{'='*60}
""")

    except Exception as e:
        print(f"\n  Registration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

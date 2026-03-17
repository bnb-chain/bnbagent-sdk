"""
Discover agents from ERC-8004 Identity Registry.

Lists all registered agents and allows selection of a provider for APEX jobs.
Uses the bnbagent SDK to query the on-chain ERC-8004 registry on BSC testnet.

Usage:
    python scripts/discover_agent.py [agent_name_filter]

Examples:
    python scripts/discover_agent.py           # List all agents
    python scripts/discover_agent.py news      # Filter by name containing "news"
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from demo root directory (.env next to scripts/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def discover_agents(name_filter: str = "") -> list:
    """
    Discover agents from ERC-8004 registry.

    Args:
        name_filter: Optional filter for agent names (case-insensitive)

    Returns:
        List of agent dicts with: token_id, name, description, owner_address, services
    """
    try:
        from bnbagent import ERC8004Agent, EVMWalletProvider
    except ImportError:
        print("Error: bnbagent SDK not installed")
        print("Run: pip install bnbagent")
        sys.exit(1)

    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD", "demo-password")

    if not private_key:
        print("Error: PRIVATE_KEY required in .env")
        sys.exit(1)

    wallet = EVMWalletProvider(
        password=wallet_password,
        private_key=private_key,
    )

    sdk = ERC8004Agent(
        network="bsc-testnet",
        wallet_provider=wallet,
        debug=False,
    )

    all_agents = []
    offset = 0
    limit = 50

    while True:
        result = sdk.get_all_agents(limit=limit, offset=offset)
        items = result.get("items", [])
        all_agents.extend(items)

        if len(items) < limit:
            break
        offset += limit

    if name_filter:
        name_filter_lower = name_filter.lower()
        all_agents = [
            a for a in all_agents
            if name_filter_lower in a.get("name", "").lower()
        ]

    return all_agents


def get_agent_apex_endpoint(agent: dict) -> str | None:
    """Extract APEX endpoint from agent services."""
    services = agent.get("services", {})
    for service_name, service_info in services.items():
        endpoint = service_info.get("endpoint", "")
        if "apex" in endpoint.lower() or "apex" in service_name.lower():
            return endpoint
    # Fallback: first endpoint
    for service_name, service_info in services.items():
        endpoint = service_info.get("endpoint", "")
        if endpoint:
            # Convert status endpoint to base URL
            if endpoint.endswith("/status"):
                return endpoint.rsplit("/status", 1)[0]
            return endpoint
    return None


def main():
    name_filter = sys.argv[1] if len(sys.argv) > 1 else ""

    print(f"""
{'='*60}
  ERC-8004 Agent Discovery
{'='*60}
""")

    agents = discover_agents(name_filter)

    if not agents:
        print("  No agents found.")
        if name_filter:
            print(f"  (filter: '{name_filter}')")
        sys.exit(0)

    print(f"  Found {len(agents)} agent(s):\n")

    for i, agent in enumerate(agents, 1):
        apex_endpoint = get_agent_apex_endpoint(agent)
        print(f"  [{i}] Agent #{agent['token_id']}: {agent['name']}")
        print(f"      Owner:       {agent['owner_address']}")
        print(f"      Description: {agent.get('description', 'N/A')[:60]}...")
        if apex_endpoint:
            print(f"      APEX URL:    {apex_endpoint}")
        print()

    if len(agents) == 1:
        selected = agents[0]
        print(f"  Auto-selected: Agent #{selected['token_id']} ({selected['name']})")
    else:
        print("  Enter agent number to select (or press Enter to exit):")
        choice = input("  > ").strip()
        if not choice:
            sys.exit(0)
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(agents):
                print("  Invalid choice")
                sys.exit(1)
            selected = agents[idx]
        except ValueError:
            print("  Invalid input")
            sys.exit(1)

    apex_endpoint = get_agent_apex_endpoint(selected)
    print(f"""
{'='*60}
  Selected Agent
{'='*60}
  Agent ID:    {selected['token_id']}
  Name:        {selected['name']}
  Owner:       {selected['owner_address']}
  APEX URL:    {apex_endpoint or 'N/A'}

  For .env, use:
    AGENT_B_ADDRESS={selected['owner_address']}
    AGENT_B_URL={apex_endpoint or 'http://localhost:8002'}
    AGENT_ID={selected['token_id']}

{'='*60}
""")


if __name__ == "__main__":
    main()

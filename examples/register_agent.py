"""
Register Agent - One-time on-chain agent registration.

This script registers your agent on the ERC-8004 Identity Registry.
Run this once to get your agent's on-chain ID.

Usage:
    python register_agent.py

Environment:
    PRIVATE_KEY          - Agent wallet private key
    WALLET_PASSWORD      - Password for keystore encryption
    AGENT_NAME           - Agent name (default: "my-agent")
    AGENT_DESCRIPTION    - Agent description
    AGENT_ENDPOINT       - Agent card URL

Example .env:
    PRIVATE_KEY=0x...
    WALLET_PASSWORD=secure-password
    AGENT_NAME=blockchain-news-agent
    AGENT_DESCRIPTION=AI agent for blockchain news analysis
    AGENT_ENDPOINT=https://my-agent.example.com/.well-known/agent-card.json
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def main():
    # Check required env vars
    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD")
    
    if not private_key:
        print("Error: PRIVATE_KEY environment variable is required")
        sys.exit(1)
    
    if not wallet_password:
        print("Error: WALLET_PASSWORD environment variable is required")
        sys.exit(1)
    
    # Get agent details
    agent_name = os.getenv("AGENT_NAME", "my-agent")
    agent_description = os.getenv(
        "AGENT_DESCRIPTION",
        "AI agent registered via BNBAgent SDK"
    )
    agent_endpoint = os.getenv(
        "AGENT_ENDPOINT",
        "https://example.com/.well-known/agent-card.json"
    )
    
    print("=" * 60)
    print("ERC-8004 Agent Registration")
    print("=" * 60)
    print()
    print(f"Name:        {agent_name}")
    print(f"Description: {agent_description}")
    print(f"Endpoint:    {agent_endpoint}")
    print()
    
    # Confirm
    confirm = input("Proceed with registration? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        sys.exit(0)
    
    print()
    print("Initializing SDK...")
    
    try:
        from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider
    except ImportError:
        print("Error: bnbagent SDK not installed")
        print("Run: pip install git+https://github.com/bnb-chain/bnbagent-sdk.git")
        sys.exit(1)
    
    # Create wallet
    wallet = EVMWalletProvider(
        password=wallet_password,
        private_key=private_key,
    )
    
    # Initialize SDK
    sdk = ERC8004Agent(
        network="bsc-testnet",
        wallet_provider=wallet,
        debug=True,
    )
    
    print(f"Wallet address: {sdk.wallet_address}")
    print()
    
    # Generate agent URI
    print("Generating agent metadata...")
    
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
    
    # Check if already registered
    print("Checking existing registration...")
    
    try:
        agents = sdk.get_all_agents(limit=100, offset=0)
        for agent in agents.get("items", []):
            if (
                agent.get("name", "").lower() == agent_name.lower()
                and agent.get("owner_address", "").lower() == sdk.wallet_address.lower()
            ):
                print()
                print(f"Agent already registered!")
                print(f"  Agent ID: {agent['token_id']}")
                print(f"  Name:     {agent['name']}")
                print()
                
                # Check if URI needs update
                if agent.get("agent_uri") != agent_uri:
                    update = input("URI changed. Update on-chain? (y/N): ").strip().lower()
                    if update == "y":
                        print("Updating agent URI...")
                        result = sdk.set_agent_uri(agent["token_id"], agent_uri)
                        print(f"Updated! TX: {result['transactionHash']}")
                
                sys.exit(0)
    except Exception as e:
        print(f"Warning: Could not check existing registrations: {e}")
    
    # Register
    print()
    print("Registering agent on-chain...")
    print("(This will cost gas)")
    print()
    
    try:
        result = sdk.register_agent(agent_uri=agent_uri)
        
        print("=" * 60)
        print("Registration Successful!")
        print("=" * 60)
        print()
        print(f"Agent ID:        {result['agentId']}")
        print(f"Transaction:     {result['transactionHash']}")
        print(f"Owner:           {sdk.wallet_address}")
        print()
        print("View on explorer:")
        print(f"  https://testnet.bscscan.com/tx/{result['transactionHash']}")
        print()
        print("Next steps:")
        print("  1. Save your Agent ID for future reference")
        print("  2. Start your agent server")
        print("  3. Clients can now create jobs for your agent")
        
    except Exception as e:
        print(f"Registration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

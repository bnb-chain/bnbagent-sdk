"""
BSC Testnet Usage Example

This example demonstrates how to use the ERC8004Agent SDK with BSC Testnet.

IMPORTANT:
- Gas fees are sponsored by MegaFuel Paymaster (no BNB required)
- All wallets are stored encrypted using Keystore V3 format
- Set WALLET_PASSWORD environment variable before running

Usage:
    WALLET_PASSWORD=your-password python examples/testnet_usage.py

Password Recovery:
    If you forget your password, you cannot decrypt the existing wallet.
    Options:
    1. Delete .bnbagent_state and create a new wallet (loses old address)
    2. If you have a backup private key, import it with a new password
"""

import os
import getpass
from bnbagent import ERC8004Agent, EVMWalletProvider, AgentEndpoint


def main():
    """Register or update an agent on BSC Testnet."""

    print("ERC8004Agent SDK - BSC Testnet Example\n")

    # Get password from environment variable, or prompt interactively
    password = os.getenv("WALLET_PASSWORD")
    if not password:
        password = getpass.getpass("Enter wallet password: ")

    # Create wallet provider
    print("Creating wallet provider...")
    wallet = EVMWalletProvider(password=password, debug=True)

    # Initialize SDK with wallet provider
    print("Initializing SDK...")
    sdk = ERC8004Agent(
        wallet_provider=wallet,
        network="bsc-testnet",
        debug=True,
    )
    print(f"Wallet: {sdk.wallet_address}")
    print(f"Contract: {sdk.contract_address}\n")

    # Define agent configuration
    agent_name = "My Testnet Agent 06"
    agent_uri = sdk.generate_agent_uri(
        name=agent_name,
        description="A test agent running on BSC Testnet",
        # image = https://example.com/agent.png
        image="",
        endpoints=[
            AgentEndpoint(
                name="A2A",
                endpoint="https://agent.example/.well-known/agent-card.json",
                version="0.3.0",
            )
        ],
    )

    # Check local state to avoid duplicate registration
    local_info = sdk.get_local_agent_info(agent_name)

    try:
        if local_info:
            # Agent exists - check if update needed
            agent_id = local_info["agent_id"]
            print(f"Agent '{agent_name}' found (ID: {agent_id})")

            if local_info["agent_uri"] != agent_uri:
                print("Agent URI changed, updating...")
                result = sdk.set_agent_uri(agent_id, agent_uri)
                print(f"✓ Updated: {result.get('transactionHash')}\n")
            else:
                print("Agent URI unchanged, no update needed.\n")
        else:
            # New agent - register
            print(f"Registering new agent '{agent_name}'...")
            result = sdk.register_agent(agent_uri=agent_uri)
            agent_id = result["agentId"]
            print(f"✓ Registered! ID: {agent_id}")
            print(f"  TX: https://testnet.bscscan.com/tx/{result['transactionHash']}\n")

        # Query agent info from chain
        info = sdk.get_agent_info(agent_id)
        print(f"On-chain Info:")
        print(f"  Address: {info['agentAddress']}")
        print(f"  Owner: {info['owner']}")

        # Parse and display agent data
        agent_data = sdk.parse_agent_uri(info["agentURI"])
        if agent_data:
            print(f"  Name: {agent_data.get('name')}")
            print(f"  Description: {agent_data.get('description')}")

    except Exception as e:
        print(f"✗ Error: {e}")


if __name__ == "__main__":
    main()

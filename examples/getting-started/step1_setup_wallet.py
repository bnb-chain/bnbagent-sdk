"""
Step 1: Setup Wallet

Creates a wallet from your private key and checks balances.
If needed, mints testnet U tokens for APEX payments.

Prerequisites:
    - Private key with testnet BNB (get from https://www.bnbchain.org/en/testnet-faucet)

Usage:
    cp .env.example .env  # Fill in PRIVATE_KEY
    python step1_setup_wallet.py

Next: step2_register_agent.py
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
    payment_token_address = os.getenv("PAYMENT_TOKEN_ADDRESS", "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565")

    if not private_key or private_key == "0x...":
        print("Error: Set PRIVATE_KEY in your .env file")
        print("  cp .env.example .env")
        print("  # Then edit .env and paste your private key")
        sys.exit(1)

    print("=" * 50)
    print("Step 1: Setup Wallet")
    print("=" * 50)
    print()

    # --- Create wallet ---
    from bnbagent import EVMWalletProvider

    wallet = EVMWalletProvider(
        password=wallet_password,
        private_key=private_key,
    )

    address = wallet.address
    print(f"Wallet address: {address}")
    print()

    # --- Check BNB balance ---
    from web3 import Web3

    rpc_url = os.getenv("RPC_URL", "https://data-seed-prebsc-2-s2.binance.org:8545/")
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    # Inject POA middleware for BSC
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except ImportError:
        from web3.middleware import geth_poa_middleware
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    bnb_balance = w3.eth.get_balance(address)
    bnb_display = w3.from_wei(bnb_balance, "ether")
    print(f"BNB balance: {bnb_display} BNB")

    if bnb_balance == 0:
        print()
        print("You need testnet BNB for gas fees!")
        print("Get some from: https://www.bnbchain.org/en/testnet-faucet")
        print("Then run this script again.")
        sys.exit(1)

    # --- Check U token balance ---
    # Minimal BEP20 ABI for balanceOf and allocateTo
    erc20_abi = [
        {
            "inputs": [{"name": "account", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [
                {"name": "ownerAddress", "type": "address"},
                {"name": "value", "type": "uint256"},
            ],
            "name": "allocateTo",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    token = w3.eth.contract(
        address=Web3.to_checksum_address(payment_token_address),
        abi=erc20_abi,
    )

    token_balance = token.functions.balanceOf(address).call()
    decimals = token.functions.decimals().call()
    token_display = token_balance / (10 ** decimals)
    print(f"U token balance: {token_display} U")

    # --- Mint test tokens if balance is 0 ---
    if token_balance == 0:
        print()
        print("No U tokens found. Minting 100 test tokens...")

        mint_amount = 100 * (10 ** decimals)
        account = w3.eth.account.from_key(private_key)

        tx = token.functions.allocateTo(address, mint_amount).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
        })

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] == 1:
            new_balance = token.functions.balanceOf(address).call()
            print(f"Minted! New balance: {new_balance / (10 ** decimals)} U")
            print(f"TX: https://testnet.bscscan.com/tx/{tx_hash.hex()}")
        else:
            print("Mint transaction failed. Check your BNB balance for gas.")
            sys.exit(1)

    print()
    print("Wallet is ready!")
    print(f"  Address: {address}")
    print(f"  BNB:     {bnb_display}")
    print()
    print("Next: python step2_register_agent.py")


if __name__ == "__main__":
    main()

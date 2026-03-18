"""
Step 4: Create and Fund a Job

Creates an APEX job, sets a budget, approves token spending,
funds the job, and polls until the agent submits.

Run this in Terminal 2 while step3 agent is running in Terminal 1.

Prerequisites:
    - step3_run_agent.py running in another terminal
    - U tokens in your wallet (step1 mints them)

Usage:
    python step4_create_job.py

Environment (optional overrides):
    AGENT_ADDRESS  - Provider agent address (defaults to your own wallet)

Next: step5_settle_job.py
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Load .env from this script's directory
load_dotenv(Path(__file__).resolve().parent / ".env")


def main():
    # --- Load wallet (from keystore or env) ---
    from bnbagent import EVMWalletProvider, APEXClient, APEXStatus
    from bnbagent.apex import get_default_expiry
    from bnbagent.config import resolve_network
    from bnbagent.core import create_web3, load_erc20_abi
    from web3 import Web3

    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD", "quickstart-demo")

    if private_key and private_key != "0x...":
        wallet = EVMWalletProvider(password=wallet_password, private_key=private_key)
    elif EVMWalletProvider.keystore_exists():
        wallet = EVMWalletProvider(password=wallet_password)
    else:
        print("Error: Run step1_setup_wallet.py first to import your private key")
        sys.exit(1)

    # Network defaults — same as step1-step3, no extra env vars required
    _nc = resolve_network()
    rpc_url = os.getenv("RPC_URL") or _nc.rpc_url
    erc8183_address = os.getenv("ERC8183_ADDRESS") or _nc.erc8183_contract
    evaluator_address = os.getenv("APEX_EVALUATOR_ADDRESS") or _nc.apex_evaluator
    payment_token_address = os.getenv("PAYMENT_TOKEN_ADDRESS") or _nc.payment_token

    print("=" * 50)
    print("Step 4: Create and Fund a Job")
    print("=" * 50)
    print()

    # --- Initialize Web3 and clients ---
    w3 = create_web3(rpc_url)

    apex = APEXClient(
        web3=w3,
        contract_address=erc8183_address,
        wallet_provider=wallet,
    )

    agent_address = os.getenv("AGENT_ADDRESS", wallet.address)

    print(f"Client:    {wallet.address}")
    print(f"Provider:  {agent_address}")
    print(f"Evaluator: {evaluator_address}")
    print()

    # =========================================================
    # Step 4a: Create Job
    # =========================================================

    print("-" * 50)
    print("4a: Creating job...")
    print("-" * 50)

    expiry = get_default_expiry()  # ~73 hours from now
    description = "Quickstart demo task: analyze blockchain trends"

    result = apex.create_job(
        provider=agent_address,
        evaluator=evaluator_address,
        expired_at=expiry,
        description=description,
        hook=evaluator_address,  # APEX evaluator as hook for auto-assertion
    )

    job_id = result["jobId"]
    print(f"Created job #{job_id}")
    print(f"TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
    print()

    # =========================================================
    # Step 4b: Set Budget
    # =========================================================

    print("-" * 50)
    print("4b: Setting budget...")
    print("-" * 50)

    budget = 1 * 10**18  # 1 U token
    print(f"Budget: {budget / 10**18} U")

    result = apex.set_budget(job_id, budget)
    print(f"TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
    print()

    # =========================================================
    # Step 4c: Approve BEP20 spending
    # =========================================================

    print("-" * 50)
    print("4c: Approving token spend...")
    print("-" * 50)

    token = w3.eth.contract(
        address=Web3.to_checksum_address(payment_token_address),
        abi=load_erc20_abi(),
    )

    tx = token.functions.approve(
        Web3.to_checksum_address(erc8183_address), budget
    ).build_transaction({
        "from": wallet.address,
        "nonce": w3.eth.get_transaction_count(wallet.address),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })

    signed = wallet.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed["rawTransaction"])
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] != 1:
        print("Approve transaction failed!")
        sys.exit(1)

    print(f"Approved {budget / 10**18} U for ERC-8183 contract")
    print(f"TX: https://testnet.bscscan.com/tx/{tx_hash.hex()}")
    print()

    # =========================================================
    # Step 4d: Fund Job
    # =========================================================

    print("-" * 50)
    print("4d: Funding job...")
    print("-" * 50)

    result = apex.fund(job_id, budget)
    print(f"Funded job #{job_id} with {budget / 10**18} U")
    print(f"TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
    print()

    # =========================================================
    # Step 4e: Poll for agent submission
    # =========================================================

    print("-" * 50)
    print("4e: Waiting for agent to process and submit...")
    print("-" * 50)
    print("(Checking every 10 seconds)")
    print()

    while True:
        job = apex.get_job(job_id)
        status = APEXStatus(job["status"])

        if status == APEXStatus.FUNDED:
            print(f"  Status: FUNDED (waiting for agent...)")
            time.sleep(10)
            continue
        elif status == APEXStatus.SUBMITTED:
            print(f"  Status: SUBMITTED -- agent has submitted!")
            break
        elif status == APEXStatus.COMPLETED:
            print(f"  Status: COMPLETED -- job done!")
            sys.exit(0)
        elif status == APEXStatus.REJECTED:
            print(f"  Status: REJECTED")
            sys.exit(1)
        else:
            print(f"  Status: {status.name}")
            time.sleep(10)
            continue

    # =========================================================
    # Step 4f: Fetch and verify deliverable
    # =========================================================

    print("-" * 50)
    print("4f: Fetching deliverable...")
    print("-" * 50)

    job = apex.get_job(job_id)
    deliverable_hash = job["deliverable"]
    print(f"  On-chain deliverable hash: 0x{deliverable_hash.hex()}")

    # Fetch deliverable using SDK storage provider
    import asyncio
    from bnbagent.storage import storage_provider_from_env

    storage = storage_provider_from_env()
    data_url = f"file://.agent-data/job-{job_id}.json"
    try:
        deliverable_data = asyncio.run(storage.download(data_url))

        # Verify hash
        computed_hash = Web3.keccak(text=data_url)

        if computed_hash == deliverable_hash:
            print("  Hash verification: PASSED")
        else:
            print("  Hash verification: MISMATCH (expected on-chain != computed)")

        # Display agent response
        response_content = deliverable_data.get("response", "")
        print()
        print("  Agent response:")
        for line in response_content.split("\n"):
            print(f"    {line}")
    except Exception:
        print(f"  Could not fetch deliverable from storage")
        print("  (This is expected if agent uses IPFS or runs on a different machine)")

    print()
    print(f"Job #{job_id} is submitted and ready for settlement.")
    print()
    print(f"Next: python step5_settle_job.py {job_id}")


if __name__ == "__main__":
    main()

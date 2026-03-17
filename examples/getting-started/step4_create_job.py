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
    # --- Check required env vars ---
    rpc_url = os.getenv("RPC_URL")
    erc8183_address = os.getenv("ERC8183_ADDRESS")
    evaluator_address = os.getenv("APEX_EVALUATOR_ADDRESS")
    private_key = os.getenv("PRIVATE_KEY")
    payment_token_address = os.getenv("PAYMENT_TOKEN_ADDRESS", "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565")

    missing = []
    if not rpc_url:
        missing.append("RPC_URL")
    if not erc8183_address:
        missing.append("ERC8183_ADDRESS")
    if not evaluator_address:
        missing.append("APEX_EVALUATOR_ADDRESS")
    if not private_key:
        missing.append("PRIVATE_KEY")

    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        print("Make sure your .env file is set up (see .env.example)")
        sys.exit(1)

    print("=" * 50)
    print("Step 4: Create and Fund a Job")
    print("=" * 50)
    print()

    # --- Initialize Web3 and clients ---
    from web3 import Web3
    from bnbagent import APEXClient, APEXStatus, get_default_expiry

    # Inject POA middleware for BSC
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        poa_middleware = ExtraDataToPOAMiddleware
    except ImportError:
        from web3.middleware import geth_poa_middleware
        poa_middleware = geth_poa_middleware

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(poa_middleware, layer=0)

    apex = APEXClient(
        web3=w3,
        contract_address=erc8183_address,
        private_key=private_key,
    )

    # The provider address -- defaults to your own wallet for self-testing
    account = w3.eth.account.from_key(private_key)
    agent_address = os.getenv("AGENT_ADDRESS", account.address)

    print(f"Client:    {account.address}")
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

    # Minimal BEP20 approve ABI
    erc20_abi = [
        {
            "inputs": [
                {"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "name": "approve",
            "outputs": [{"name": "", "type": "bool"}],
            "stateMutability": "nonpayable",
            "type": "function",
        },
    ]

    token = w3.eth.contract(
        address=Web3.to_checksum_address(payment_token_address),
        abi=erc20_abi,
    )

    tx = token.functions.approve(
        Web3.to_checksum_address(erc8183_address), budget
    ).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
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

    # Try to read deliverable from local storage
    storage_path = os.getenv("LOCAL_STORAGE_PATH", "./.agent-data")
    deliverable_file = os.path.join(storage_path, f"job-{job_id}.json")

    import json

    if os.path.isfile(deliverable_file):
        with open(deliverable_file, "r") as f:
            deliverable_data = json.load(f)

        # Verify hash
        data_url = f"file://{os.path.abspath(deliverable_file)}"
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
    else:
        print(f"  Local file not found: {deliverable_file}")
        print("  (This is expected if agent uses IPFS or runs on a different machine)")

    print()
    print(f"Job #{job_id} is submitted and ready for settlement.")
    print()
    print(f"Next: python step5_settle_job.py {job_id}")


if __name__ == "__main__":
    main()

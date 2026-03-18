"""
Step 5: Settle Job

Settles a submitted job after the APEX Evaluator liveness period expires.
Anyone can call settle -- it finalizes the job and pays the agent.

Prerequisites:
    - A submitted job from step4 (job ID required)

Usage:
    python step5_settle_job.py <JOB_ID>
    # or
    JOB_ID=42 python step5_settle_job.py

Environment:
    JOB_ID  - The job ID to settle (or pass as CLI argument)
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Load .env from this script's directory
load_dotenv(Path(__file__).resolve().parent / ".env")


def main():
    # --- Get job ID from CLI arg or env ---
    job_id = None
    if len(sys.argv) > 1:
        job_id = int(sys.argv[1])
    elif os.getenv("JOB_ID"):
        job_id = int(os.getenv("JOB_ID"))
    else:
        print("Usage: python step5_settle_job.py <JOB_ID>")
        print("  or:  JOB_ID=42 python step5_settle_job.py")
        sys.exit(1)

    # --- Load wallet (from keystore or env) ---
    from bnbagent import EVMWalletProvider
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
    from bnbagent.config import resolve_network

    _nc = resolve_network()
    rpc_url = os.getenv("RPC_URL") or _nc.rpc_url
    erc8183_address = os.getenv("ERC8183_ADDRESS") or _nc.erc8183_contract
    evaluator_address = os.getenv("APEX_EVALUATOR_ADDRESS") or _nc.apex_evaluator

    print("=" * 50)
    print(f"Step 5: Settle Job #{job_id}")
    print("=" * 50)
    print()

    # --- Initialize ---
    from web3 import Web3
    from bnbagent import APEXClient, APEXStatus
    from bnbagent.apex import APEXEvaluatorClient
    from bnbagent.core import create_web3, load_erc20_abi

    w3 = create_web3(rpc_url)

    apex = APEXClient(
        web3=w3,
        contract_address=erc8183_address,
        wallet_provider=wallet,
    )

    evaluator = APEXEvaluatorClient(
        web3=w3,
        contract_address=evaluator_address,
        wallet_provider=wallet,
    )

    # --- Check current job status ---
    print("Checking job status...")
    job = apex.get_job(job_id)
    status = APEXStatus(job["status"])
    print(f"  Current status: {status.name}")

    if status == APEXStatus.COMPLETED:
        print("  Job already completed!")
        return

    if status == APEXStatus.REJECTED:
        print("  Job was rejected.")
        return

    if status != APEXStatus.SUBMITTED:
        print(f"  Job must be in SUBMITTED status to settle (current: {status.name})")
        sys.exit(1)

    print()

    # --- Check assertion info ---
    print("Checking APEX Evaluator assertion...")
    info = evaluator.get_assertion_info(job_id)

    if not info.initiated:
        print("  Assertion not yet initiated. The agent may still be processing.")
        print("  Wait for the agent to submit, then try again.")
        sys.exit(1)

    if info.disputed:
        print("  Assertion is DISPUTED!")
        print(f"  Assertion ID: {info.assertion_id.hex()}")
        print("  The UMA DVM will vote on the outcome. This takes 48-96 hours.")
        sys.exit(0)

    print(f"  Assertion ID: {info.assertion_id.hex()}")
    print(f"  Liveness end: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info.liveness_end))}")
    print()

    # --- Wait for liveness period if needed ---
    if not info.settleable:
        remaining = info.liveness_end - int(time.time())
        print(f"Waiting for liveness period to expire ({remaining}s remaining)...")
        print()

        while True:
            info = evaluator.get_assertion_info(job_id)

            if info.settleable:
                print("  Liveness period expired!")
                break

            remaining = max(0, info.liveness_end - int(time.time()))
            print(f"  {remaining}s remaining...")
            # Wait up to 60 seconds at a time, or until expiry + 5s buffer
            time.sleep(min(60, remaining + 5))

    print()

    # --- Snapshot balances before settle ---
    payment_token_address = os.getenv("PAYMENT_TOKEN_ADDRESS") or _nc.payment_token
    token = w3.eth.contract(
        address=Web3.to_checksum_address(payment_token_address), abi=load_erc20_abi(),
    )
    decimals = token.functions.decimals().call()

    job = apex.get_job(job_id)
    provider_addr = job["provider"]
    client_addr = job["client"]

    provider_before = token.functions.balanceOf(provider_addr).call()
    client_before = token.functions.balanceOf(client_addr).call()

    # --- Settle ---
    print("Settling job...")
    result = evaluator.settle_job(job_id)
    print(f"  Settled! TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
    print()

    # --- Final status ---
    print("-" * 50)
    print("Final Status")
    print("-" * 50)

    job = apex.get_job(job_id)
    status = APEXStatus(job["status"])
    print(f"  Job #{job_id}: {status.name}")

    if status == APEXStatus.COMPLETED:
        print("  Agent was paid successfully!")
    elif status == APEXStatus.REJECTED:
        print("  Job was rejected. Client can claim a refund.")

    # --- Balance changes ---
    provider_after = token.functions.balanceOf(provider_addr).call()
    client_after = token.functions.balanceOf(client_addr).call()
    provider_diff = (provider_after - provider_before) / (10 ** decimals)
    client_diff = (client_after - client_before) / (10 ** decimals)

    print()
    print("-" * 50)
    print("Payment Summary")
    print("-" * 50)
    print(f"  Budget:           {job['budget'] / (10 ** decimals)} U")
    print(f"  Provider ({provider_addr[:10]}...): {'+' if provider_diff >= 0 else ''}{provider_diff} U")
    print(f"  Client   ({client_addr[:10]}...): {'+' if client_diff >= 0 else ''}{client_diff} U")

    print()
    print("Quickstart complete! You have successfully:")
    print("  1. Set up a wallet with testnet tokens")
    print("  2. Registered an agent on ERC-8004")
    print("  3. Run an APEX agent server")
    print("  4. Created and funded a job")
    print("  5. Settled the job via APEX Evaluator")


if __name__ == "__main__":
    main()

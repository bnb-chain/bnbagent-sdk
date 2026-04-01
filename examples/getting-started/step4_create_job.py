"""
Step 4: Discover Agent, Create and Fund a Job

Discovers the agent from ERC-8004 registry, negotiates terms,
creates an APEX job, funds it, and triggers execution.

Run this in Terminal 2 while step2 agent is running in Terminal 1.

Prerequisites:
    - step2_run_agent.py running in another terminal
    - step3_register_agent.py completed (agent registered on ERC-8004)
    - U tokens in your wallet (step1 mints them)

Usage:
    python step4_create_job.py

Environment (optional overrides):
    AGENT_NAME     - Agent name to discover (default: getting-started-agent)
    AGENT_ADDRESS  - Skip discovery, use this address directly

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

    # =========================================================
    # Step 4a: Discover agent from ERC-8004 registry
    # =========================================================

    print("-" * 50)
    print("4a: Discovering agent from ERC-8004 registry...")
    print("-" * 50)

    from bnbagent import ERC8004Agent

    agent_address = os.getenv("AGENT_ADDRESS", "")
    agent_url = os.getenv("AGENT_URL", "")
    agent_name = os.getenv("AGENT_NAME", "getting-started-agent")

    if agent_address:
        print(f"  Using AGENT_ADDRESS from env: {agent_address}")
        if not agent_url:
            agent_url = "http://localhost:8000"
    else:
        # Discover from ERC-8004 registry
        discovery_sdk = ERC8004Agent(
            wallet_provider=wallet,
            network="bsc-testnet",
        )

        print(f"  Searching for agent '{agent_name}' on ERC-8004...")
        agents = discovery_sdk.get_all_agents(limit=100, offset=0)
        found = None
        for agent in agents.get("items", []):
            if agent.get("name", "").lower() == agent_name.lower():
                found = agent
                break

        if found:
            agent_id = found["token_id"]
            agent_address = found["owner_address"]
            print(f"  Found agent #{agent_id}: {found.get('name')}")
            print(f"  Owner:    {agent_address}")

            # Extract endpoint URL from agent services
            services = found.get("services", {})
            for svc_name, svc_info in services.items():
                endpoint = svc_info.get("endpoint", "")
                if endpoint:
                    # Derive base URL from endpoint (strip path like /.well-known/...)
                    from urllib.parse import urlparse
                    parsed = urlparse(endpoint)
                    agent_url = f"{parsed.scheme}://{parsed.netloc}"
                    print(f"  Endpoint: {agent_url} (from {svc_name} service)")
                    break

            if not agent_url:
                agent_url = "http://localhost:8000"
                print(f"  No endpoint in registry, using default: {agent_url}")
        else:
            print(f"  Agent '{agent_name}' not found on ERC-8004.")
            print("  Make sure step3_register_agent.py was run.")
            print("  Falling back to own wallet address...")
            agent_address = wallet.address
            agent_url = "http://localhost:8000"

    print()
    print(f"  Client:    {wallet.address}")
    print(f"  Provider:  {agent_address}")
    print(f"  Evaluator: {evaluator_address}")
    print(f"  Agent URL: {agent_url}")
    print()

    # =========================================================
    # Step 4b: Negotiate with agent (optional — falls back to plain description)
    # =========================================================

    print("-" * 50)
    print("4b: Negotiating with agent...")
    print("-" * 50)

    import httpx
    from bnbagent.apex.negotiation import build_job_description

    task_description = "Quickstart demo task: analyze blockchain trends"
    neg_result = None

    negotiate_payload = {
        "task_description": task_description,
        "terms": {
            "service_type": "general",
            "deliverables": "Analysis report",
            "quality_standards": "Clear and accurate",
        },
    }

    try:
        resp = httpx.post(
            f"{agent_url}/apex/negotiate",
            json=negotiate_payload,
            timeout=15,
        )
        if resp.status_code == 200:
            neg_result = resp.json()
            print(f"  Negotiation accepted. Price: {neg_result.get('price', 'N/A')}")
        else:
            print(f"  Negotiation returned {resp.status_code}, using plain description")
    except Exception as e:
        print(f"  Negotiation failed ({e}), using plain description")

    description = build_job_description(neg_result) if neg_result and neg_result.get("accepted") else task_description
    print(f"  Description: {'structured JSON (negotiated)' if neg_result else 'plain text (fallback)'}")
    print()

    # =========================================================
    # Step 4c: Create Job
    # =========================================================

    print("-" * 50)
    print("4c: Creating job...")
    print("-" * 50)

    expiry = get_default_expiry()  # ~73 hours from now

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
    # Step 4d: Set Budget
    # =========================================================

    print("-" * 50)
    print("4d: Setting budget...")
    print("-" * 50)

    budget = 1 * 10**18  # 1 U token
    print(f"Budget: {budget / 10**18} U")

    result = apex.set_budget(job_id, budget)
    print(f"TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
    print()

    # =========================================================
    # Step 4e: Approve BEP20 spending
    # =========================================================

    print("-" * 50)
    print("4e: Approving token spend...")
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
    # Step 4f: Fund Job
    # =========================================================

    print("-" * 50)
    print("4f: Funding job...")
    print("-" * 50)

    result = apex.fund(job_id, budget)
    print(f"Funded job #{job_id} with {budget / 10**18} U")
    print(f"TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")
    print()

    # =========================================================
    # Step 4g: Trigger execution and poll for submission
    # =========================================================

    print("-" * 50)
    print("4g: Triggering agent execution via /job/execute...")
    print("-" * 50)

    agent_url = os.getenv("AGENT_URL", "http://localhost:8000")
    job_timeout = int(os.getenv("JOB_TIMEOUT", "30"))

    try:
        resp = httpx.post(
            f"{agent_url}/apex/job/execute",
            json={"job_id": job_id, "timeout": job_timeout},
            timeout=job_timeout + 10,
        )
        print(f"  /job/execute returned {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Job completed! TX: {data.get('txHash', 'N/A')}")
        elif resp.status_code == 202:
            print("  Job accepted, processing in background. Polling...")
        else:
            print(f"  Unexpected response: {resp.text[:200]}")
    except Exception as e:
        print(f"  /job/execute failed ({e}), falling back to polling...")

    print()
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
    # Step 4h: Fetch and verify deliverable
    # =========================================================

    print("-" * 50)
    print("4h: Fetching deliverable...")
    print("-" * 50)

    job = apex.get_job(job_id)

    # --- Method 1: Fetch via agent's HTTP API (recommended) ---
    # In production, client and agent are on different machines.
    # The agent exposes GET /apex/job/{id}/response for clients to retrieve results.
    import json
    import urllib.request

    agent_url = os.getenv("AGENT_URL", "http://localhost:8000")
    response_url = f"{agent_url}/apex/job/{job_id}/response"
    deliverable_data = None

    try:
        req = urllib.request.Request(response_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            deliverable_data = json.loads(resp.read())
        print(f"  Fetched via agent API: {response_url}")
    except Exception as e:
        print(f"  Agent API unavailable ({e}), trying local storage...")

    # --- Method 2: Fallback to local storage (same-machine only) ---
    if deliverable_data is None:
        import asyncio
        from bnbagent.storage import storage_provider_from_env

        storage = storage_provider_from_env()
        data_url = f"file://.agent-data/job-{job_id}.json"
        try:
            deliverable_data = asyncio.run(storage.download(data_url))
            print(f"  Fetched from local storage: {data_url}")
        except Exception:
            print("  Could not fetch deliverable from storage")
            print("  (This is expected if agent uses IPFS or runs on a different machine)")

    # --- Display result ---
    if deliverable_data:
        response_content = deliverable_data.get("response", "")
        print()
        print("  Agent response:")
        for line in response_content.split("\n"):
            print(f"    {line}")

    print()
    print(f"Job #{job_id} is submitted and ready for settlement.")
    print()
    print(f"Next: python step5_settle_job.py {job_id}")


if __name__ == "__main__":
    main()

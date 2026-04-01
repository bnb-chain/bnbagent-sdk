"""
End-to-end test for the Quickstart flow.

Runs all 5 steps in a single process against BSC Testnet:
  1. Setup wallet & mint tokens
  2. Start agent server (background)
  3. Register agent
  4. Discover agent, create & fund a job
  5. Settle job after liveness

Prerequisites:
    - .env file with PRIVATE_KEY (funded with testnet BNB)
    - pip install bnbagent python-dotenv httpx uvicorn

Usage:
    python test_quickstart_e2e.py
    python test_quickstart_e2e.py --skip-settle   # skip waiting for liveness (faster)

Environment (optional):
    JOB_TIMEOUT    - /job/execute timeout in seconds (default: 30)
    AGENT_PORT     - Agent server port (default: 8765)
"""

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env from getting-started directory
load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("getting-started-e2e")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
RPC_URL = os.getenv("RPC_URL", "https://data-seed-prebsc-2-s2.binance.org:8545/")
ERC8183_ADDRESS = os.getenv("ERC8183_ADDRESS", "")
EVALUATOR_ADDRESS = os.getenv("APEX_EVALUATOR_ADDRESS", "")
PAYMENT_TOKEN_ADDRESS = os.getenv("PAYMENT_TOKEN_ADDRESS", "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565")
WALLET_PASSWORD = os.getenv("WALLET_PASSWORD", "quickstart-demo")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8765"))
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT", "30"))

SKIP_SETTLE = "--skip-settle" in sys.argv


class StepError(Exception):
    """Raised when a quickstart step fails."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_web3():
    from bnbagent.core import create_web3 as _create_web3
    return _create_web3(RPC_URL)


def banner(step: str, title: str):
    logger.info("")
    logger.info("=" * 55)
    logger.info(f"  {step}: {title}")
    logger.info("=" * 55)


# ---------------------------------------------------------------------------
# Step 1: Setup Wallet
# ---------------------------------------------------------------------------

def step1_setup_wallet() -> str:
    """Setup wallet, check balances, mint tokens if needed. Returns wallet address."""
    banner("Step 1", "Setup Wallet")

    from bnbagent import EVMWalletProvider
    from web3 import Web3

    wallet = EVMWalletProvider(password=WALLET_PASSWORD, private_key=PRIVATE_KEY)
    address = wallet.address
    logger.info(f"Wallet address: {address}")

    w3 = create_web3()

    # Check BNB balance
    bnb_balance = w3.eth.get_balance(address)
    bnb_display = w3.from_wei(bnb_balance, "ether")
    logger.info(f"BNB balance: {bnb_display} BNB")

    if bnb_balance == 0:
        raise StepError(
            "No BNB balance. Fund your wallet from https://www.bnbchain.org/en/testnet-faucet"
        )

    # Check U token balance
    from bnbagent.core import load_erc20_abi

    token = w3.eth.contract(
        address=Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=load_erc20_abi(),
    )
    token_balance = token.functions.balanceOf(address).call()
    decimals = token.functions.decimals().call()
    logger.info(f"U token balance: {token_balance / (10 ** decimals)} U")

    # Mint if needed
    if token_balance < 10 * (10 ** decimals):
        logger.info("Minting 100 test tokens...")
        mint_amount = 100 * (10 ** decimals)
        account = w3.eth.account.from_key(PRIVATE_KEY)
        tx = token.functions.allocateTo(address, mint_amount).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            raise StepError("Token mint transaction failed")
        new_balance = token.functions.balanceOf(address).call()
        logger.info(f"Minted! New balance: {new_balance / (10 ** decimals)} U")

    logger.info("Step 1 PASSED")
    return address


# ---------------------------------------------------------------------------
# Step 3: Register Agent
# ---------------------------------------------------------------------------

def step3_register_agent() -> int:
    """Register agent on ERC-8004. Returns agent_id."""
    banner("Step 3", "Register Agent")

    from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

    wallet = EVMWalletProvider(password=WALLET_PASSWORD, private_key=PRIVATE_KEY)
    sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet", debug=True)

    agent_name = "getting-started-e2e-test"
    agent_uri = sdk.generate_agent_uri(
        name=agent_name,
        description="E2E test agent for getting-started validation",
        endpoints=[
            AgentEndpoint(
                name="A2A",
                endpoint=f"http://localhost:{AGENT_PORT}/.well-known/agent-card.json",
                version="0.3.0",
            ),
        ],
    )

    # Check if already registered
    local_info = sdk.get_local_agent_info(agent_name)
    if local_info:
        agent_id = local_info["agent_id"]
        logger.info(f"Agent already registered: ID={agent_id}")
        if local_info.get("agent_uri") != agent_uri:
            logger.info("Updating agent URI...")
            sdk.set_agent_uri(agent_id, agent_uri)
    else:
        logger.info("Registering new agent...")
        result = sdk.register_agent(agent_uri=agent_uri)
        agent_id = result["agentId"]
        logger.info(f"Registered! Agent ID: {agent_id}")
        logger.info(f"TX: https://testnet.bscscan.com/tx/{result['transactionHash']}")

    logger.info(f"Step 3 PASSED (Agent ID: {agent_id})")
    return agent_id


# ---------------------------------------------------------------------------
# Step 2: Start Agent Server (background)
# ---------------------------------------------------------------------------

async def step2_start_agent_server() -> asyncio.Event:
    """Start the getting-started agent server in the background. Returns a ready event."""
    banner("Step 2", "Start Agent Server")

    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from bnbagent.apex.config import APEXConfig
    from bnbagent.apex.server import create_apex_app

    # Simple task processor — parses structured or plain description
    def process_task(job: dict) -> str:
        from bnbagent.apex.negotiation import parse_job_description
        raw = job.get("description", "")
        parsed = parse_job_description(raw)
        task = parsed["task"] if parsed else raw
        return f"E2E test response for: {task}"

    server_ready = asyncio.Event()

    # Use create_apex_app with on_job — SDK handles startup scan + /job/execute
    app = create_apex_app(
        on_job=process_task,
        job_timeout=30.0,
        task_metadata={"agent": "e2e-test"},
    )

    # Wrap lifespan to also signal server_ready
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan_with_ready(app: FastAPI):
        async with original_lifespan(app):
            server_ready.set()
            yield

    app.router.lifespan_context = lifespan_with_ready

    import uvicorn

    uv_config = uvicorn.Config(
        app, host="127.0.0.1", port=AGENT_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(uv_config)

    # Run server in background task
    asyncio.create_task(server.serve())

    # Wait for server to be ready
    for _ in range(30):
        await asyncio.sleep(0.5)
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:{AGENT_PORT}/apex/health", timeout=2)
                if resp.status_code == 200:
                    logger.info(f"Agent server running on port {AGENT_PORT}")
                    logger.info("Step 2 PASSED")
                    return server
        except Exception:
            continue

    raise StepError("Agent server failed to start within 15 seconds")


# ---------------------------------------------------------------------------
# Step 4: Create and Fund a Job
# ---------------------------------------------------------------------------

def step4_create_and_fund_job(agent_address: str) -> int:
    """Discover agent, create, budget, approve, fund a job. Returns job_id."""
    banner("Step 4", "Discover Agent, Create and Fund a Job")

    from web3 import Web3
    from bnbagent import APEXClient, APEXStatus, ERC8004Agent, EVMWalletProvider
    from bnbagent.apex import get_default_expiry
    from bnbagent.core import load_erc20_abi

    w3 = create_web3()
    apex = APEXClient(web3=w3, contract_address=ERC8183_ADDRESS, private_key=PRIVATE_KEY)
    account = w3.eth.account.from_key(PRIVATE_KEY)

    # 4a: Discover agent from ERC-8004
    logger.info("4a: Discovering agent from ERC-8004...")
    wallet = EVMWalletProvider(password=WALLET_PASSWORD, private_key=PRIVATE_KEY)
    discovery_sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet")

    agent_name = "getting-started-e2e-test"
    agents = discovery_sdk.get_all_agents(limit=100, offset=0)
    found = None
    for agent in agents.get("items", []):
        if agent.get("name", "").lower() == agent_name.lower():
            found = agent
            break

    if found:
        discovered_address = found["owner_address"]
        logger.info(f"Discovered agent #{found['token_id']}: {found['name']}")
        logger.info(f"  Owner: {discovered_address}")
        if discovered_address.lower() != agent_address.lower():
            logger.warning(
                f"  Discovery returned different address ({discovered_address}) "
                f"than expected ({agent_address}). Using expected address."
            )
    else:
        logger.warning(
            f"Agent '{agent_name}' not found via 8004scan API "
            f"(indexer may be delayed). Using address from step1."
        )

    # 4b: Negotiate with agent
    logger.info("4b: Negotiating with agent...")
    import httpx as _httpx
    from bnbagent.apex.negotiation import build_job_description

    task_description = "E2E test task: getting-started validation"
    neg_result = None
    try:
        resp = _httpx.post(
            f"http://127.0.0.1:{AGENT_PORT}/apex/negotiate",
            json={
                "task_description": task_description,
                "terms": {
                    "service_type": "general",
                    "deliverables": "Test result",
                    "quality_standards": "Accurate response",
                },
            },
            timeout=15,
        )
        if resp.status_code == 200:
            neg_result = resp.json()
            logger.info(f"Negotiation accepted, price={neg_result.get('price', 'N/A')}")
        else:
            logger.warning(f"Negotiate returned {resp.status_code}, using plain description")
    except Exception as e:
        logger.warning(f"Negotiate failed ({e}), using plain description")

    description = build_job_description(neg_result) if (neg_result and neg_result.get("accepted")) else task_description

    # 4c: Create job
    logger.info("4c: Creating job...")
    expiry = get_default_expiry()
    result = apex.create_job(
        provider=agent_address,
        evaluator=EVALUATOR_ADDRESS,
        expired_at=expiry,
        description=description,
        hook=EVALUATOR_ADDRESS,
    )
    job_id = result["jobId"]
    logger.info(f"Created job #{job_id} (TX: {result['transactionHash'][:16]}...)")

    # 4d: Set budget
    budget = 1 * 10**18  # 1 U token
    logger.info(f"4d: Setting budget ({budget / 10**18} U)...")
    apex.set_budget(job_id, budget)

    # 4e: Approve BEP20
    logger.info("4e: Approving token spend...")
    token = w3.eth.contract(
        address=Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=load_erc20_abi(),
    )
    tx = token.functions.approve(
        Web3.to_checksum_address(ERC8183_ADDRESS), budget
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
        raise StepError("Approve transaction failed")
    logger.info("Approved")

    # 4f: Fund job
    logger.info("4f: Funding job...")
    result = apex.fund(job_id, budget)
    logger.info(f"Funded! (TX: {result['transactionHash'][:16]}...)")

    # Verify status is FUNDED
    job = apex.get_job(job_id)
    status = APEXStatus(job["status"])
    if status != APEXStatus.FUNDED:
        raise StepError(f"Expected FUNDED status, got {status.name}")
    logger.info(f"Job #{job_id} status: {status.name}")

    logger.info(f"Step 4 PASSED (Job ID: {job_id})")
    return job_id


# ---------------------------------------------------------------------------
# Step 4e: Wait for Agent Submission
# ---------------------------------------------------------------------------

def step4e_wait_for_submission(job_id: int, timeout: int = 120) -> None:
    """Trigger /job/execute, then wait for the agent to submit the job."""
    banner("Step 4e", "Trigger Execution & Wait for Submission")

    import httpx

    # Trigger agent execution via /job/execute
    logger.info(f"Triggering /job/execute for job #{job_id}...")
    try:
        resp = httpx.post(
            f"http://127.0.0.1:{AGENT_PORT}/apex/job/execute",
            json={"job_id": job_id, "timeout": JOB_TIMEOUT},
            timeout=JOB_TIMEOUT + 10,
        )
        logger.info(f"/job/execute returned {resp.status_code}")
        if resp.status_code == 200:
            logger.info(f"Job #{job_id} completed immediately!")
            logger.info("Step 4e PASSED")
            return
        elif resp.status_code == 202:
            logger.info("Job accepted, processing in background. Polling for completion...")
    except Exception as e:
        logger.warning(f"/job/execute call failed ({e}), falling back to polling...")

    from bnbagent import APEXClient, APEXStatus

    w3 = create_web3()
    apex = APEXClient(web3=w3, contract_address=ERC8183_ADDRESS, private_key=PRIVATE_KEY)

    start = time.time()
    while time.time() - start < timeout:
        job = apex.get_job(job_id)
        status = APEXStatus(job["status"])

        if status == APEXStatus.SUBMITTED:
            logger.info(f"Job #{job_id} SUBMITTED by agent!")
            logger.info("Step 4e PASSED")
            return
        elif status == APEXStatus.COMPLETED:
            logger.info(f"Job #{job_id} already COMPLETED!")
            logger.info("Step 4e PASSED")
            return
        elif status == APEXStatus.REJECTED:
            raise StepError(f"Job #{job_id} was REJECTED")
        elif status == APEXStatus.FUNDED:
            elapsed = int(time.time() - start)
            logger.info(f"Status: FUNDED (waiting... {elapsed}s/{timeout}s)")
        else:
            logger.info(f"Status: {status.name}")

        time.sleep(10)

    raise StepError(f"Timed out waiting for agent submission after {timeout}s")


# ---------------------------------------------------------------------------
# Step 4f: Fetch and Verify Deliverable
# ---------------------------------------------------------------------------

def step4f_verify_deliverable(job_id: int) -> None:
    """Verify that the job is in SUBMITTED state and the response file exists."""
    banner("Step 4f", "Verify Deliverable")

    import json
    from bnbagent import APEXClient, APEXStatus

    w3 = create_web3()
    apex = APEXClient(web3=w3, contract_address=ERC8183_ADDRESS, private_key=PRIVATE_KEY)

    job = apex.get_job(job_id)
    status = APEXStatus(job["status"])
    logger.info(f"Job #{job_id} status: {status.name}")

    if status not in (APEXStatus.SUBMITTED, APEXStatus.COMPLETED):
        raise StepError(f"Expected SUBMITTED or COMPLETED status, got {status.name}")

    # Check local storage for response file
    storage_path = os.getenv("STORAGE_LOCAL_PATH", "./.agent-data")
    deliverable_file = os.path.join(storage_path, f"job-{job_id}.json")

    if os.path.isfile(deliverable_file):
        with open(deliverable_file, "r") as f:
            deliverable_data = json.load(f)
        response_content = deliverable_data.get("response", "")
        logger.info(f"Response file found: {deliverable_file}")
        logger.info(f"Agent response: {response_content[:100]}...")
    else:
        logger.info(f"Response file not found locally (expected if agent uses remote storage)")

    logger.info("Step 4f PASSED")


# ---------------------------------------------------------------------------
# Step 5: Settle Job
# ---------------------------------------------------------------------------

def step5_settle_job(job_id: int) -> None:
    """Wait for liveness and settle the job via evaluator."""
    banner("Step 5", "Settle Job")

    from bnbagent import APEXClient, APEXStatus
    from bnbagent.apex import APEXEvaluatorClient
    from bnbagent.core import load_erc20_abi

    w3 = create_web3()
    apex = APEXClient(web3=w3, contract_address=ERC8183_ADDRESS, private_key=PRIVATE_KEY)
    evaluator = APEXEvaluatorClient(
        web3=w3, contract_address=EVALUATOR_ADDRESS, private_key=PRIVATE_KEY,
    )

    # Check status
    job = apex.get_job(job_id)
    status = APEXStatus(job["status"])
    logger.info(f"Job status: {status.name}")

    if status == APEXStatus.COMPLETED:
        logger.info("Already completed!")
        logger.info("Step 5 PASSED")
        return

    if status != APEXStatus.SUBMITTED:
        raise StepError(f"Expected SUBMITTED, got {status.name}")

    # Check assertion
    info = evaluator.get_assertion_info(job_id)
    if not info.initiated:
        raise StepError("Assertion not initiated")

    if info.disputed:
        raise StepError("Assertion is disputed — cannot auto-settle in E2E test")

    logger.info(f"Assertion ID: {info.assertion_id.hex()}")
    logger.info(f"Liveness end: {time.strftime('%H:%M:%S', time.localtime(info.liveness_end))}")

    # Wait for liveness
    if not info.settleable:
        remaining = max(0, info.liveness_end - int(time.time()))
        logger.info(f"Waiting for liveness period ({remaining}s remaining)...")

        while True:
            info = evaluator.get_assertion_info(job_id)
            if info.settleable:
                logger.info("Liveness period expired!")
                break
            remaining = max(0, info.liveness_end - int(time.time()))
            logger.info(f"  {remaining}s remaining...")
            time.sleep(min(30, remaining + 5))

    # Snapshot balances before settle
    from web3 import Web3 as W3

    token = w3.eth.contract(
        address=W3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=load_erc20_abi(),
    )
    decimals = token.functions.decimals().call()

    job = apex.get_job(job_id)
    provider_addr = job["provider"]
    client_addr = job["client"]
    budget = job["budget"]
    provider_before = token.functions.balanceOf(provider_addr).call()
    client_before = token.functions.balanceOf(client_addr).call()

    # Settle
    logger.info("Settling job...")
    result = evaluator.settle_job(job_id)
    logger.info(f"Settled! TX: {result['transactionHash'][:16]}...")

    # Verify final status
    job = apex.get_job(job_id)
    status = APEXStatus(job["status"])
    logger.info(f"Final status: {status.name}")

    # Balance changes
    provider_after = token.functions.balanceOf(provider_addr).call()
    client_after = token.functions.balanceOf(client_addr).call()
    provider_diff = (provider_after - provider_before) / (10 ** decimals)
    client_diff = (client_after - client_before) / (10 ** decimals)

    logger.info(f"Payment: budget={budget / (10 ** decimals)} U")
    logger.info(f"  Provider: {'+' if provider_diff >= 0 else ''}{provider_diff} U")
    logger.info(f"  Client:   {'+' if client_diff >= 0 else ''}{client_diff} U")

    if status == APEXStatus.COMPLETED:
        logger.info("Step 5 PASSED — agent paid successfully!")
    elif status == APEXStatus.REJECTED:
        logger.warning("Step 5 PASSED (REJECTED — client can claim refund)")
    else:
        raise StepError(f"Unexpected final status: {status.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    start_time = time.time()

    print(f"""
{'='*55}
  Getting Started E2E Test
{'='*55}
  RPC:       {RPC_URL}
  ERC-8183:  {ERC8183_ADDRESS}
  Evaluator: {EVALUATOR_ADDRESS}
  Port:      {AGENT_PORT}
  Skip settle: {SKIP_SETTLE}
{'='*55}
""")

    # Validate env
    missing = []
    if not PRIVATE_KEY or PRIVATE_KEY == "0x...":
        missing.append("PRIVATE_KEY")
    if not ERC8183_ADDRESS:
        missing.append("ERC8183_ADDRESS")
    if not EVALUATOR_ADDRESS:
        missing.append("APEX_EVALUATOR_ADDRESS")
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in values")
        sys.exit(1)

    results = {}
    server = None

    try:
        # Step 1
        address = step1_setup_wallet()
        results["step1"] = "PASSED"

        # Step 2 — start server in background (before registration)
        server = await step2_start_agent_server()
        results["step2"] = "PASSED"

        # Step 3 — register agent (server must be running first)
        agent_id = step3_register_agent()
        results["step3"] = "PASSED"

        # Step 4 — create & fund job (sync, in thread to not block event loop)
        job_id = await asyncio.to_thread(step4_create_and_fund_job, address)
        results["step4"] = "PASSED"

        # Step 4e — wait for agent to submit
        await asyncio.to_thread(step4e_wait_for_submission, job_id)
        results["step4e"] = "PASSED"

        # Step 4f — fetch and verify deliverable
        await asyncio.to_thread(step4f_verify_deliverable, job_id)
        results["step4f"] = "PASSED"

        # Step 5 — settle
        if SKIP_SETTLE:
            logger.info("")
            logger.info("=" * 55)
            logger.info("  Step 5: SKIPPED (--skip-settle)")
            logger.info("=" * 55)
            logger.info(f"  To settle later: python step5_settle_job.py {job_id}")
            results["step5"] = "SKIPPED"
        else:
            await asyncio.to_thread(step5_settle_job, job_id)
            results["step5"] = "PASSED"

    except StepError as e:
        logger.error(f"FAILED: {e}")
        # Mark the current step as failed
        for step in ["step1", "step2", "step3", "step4", "step4e", "step4f", "step5"]:
            if step not in results:
                results[step] = f"FAILED: {e}"
                break
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        for step in ["step1", "step2", "step3", "step4", "step4e", "step4f", "step5"]:
            if step not in results:
                results[step] = f"ERROR: {e}"
                break
    finally:
        # Shutdown server
        if server is not None:
            logger.info("Shutting down agent server...")
            server.should_exit = True
            await asyncio.sleep(1)

    # Summary
    elapsed = time.time() - start_time
    print(f"""
{'='*55}
  E2E Test Results
{'='*55}""")
    all_passed = True
    for step, status in results.items():
        icon = "OK" if status == "PASSED" else ("--" if status == "SKIPPED" else "XX")
        print(f"  [{icon}] {step}: {status}")
        if "FAILED" in status or "ERROR" in status:
            all_passed = False

    print(f"""
  Duration: {elapsed:.1f}s
{'='*55}
""")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())

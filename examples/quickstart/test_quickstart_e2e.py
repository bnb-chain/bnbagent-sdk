"""
End-to-end test for the Quickstart flow.

Runs all 5 steps in a single process against BSC Testnet:
  1. Setup wallet & mint tokens
  2. Register agent
  3. Start agent server (background)
  4. Create & fund a job
  5. Settle job after liveness

Prerequisites:
    - .env file with PRIVATE_KEY (funded with testnet BNB)
    - pip install bnbagent python-dotenv httpx uvicorn

Usage:
    python test_quickstart_e2e.py
    python test_quickstart_e2e.py --skip-settle   # skip waiting for liveness (faster)

Environment (optional):
    POLL_INTERVAL  - Agent poll interval in seconds (default: 5)
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

# Load .env from quickstart directory
load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("quickstart-e2e")

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
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))

SKIP_SETTLE = "--skip-settle" in sys.argv


class StepError(Exception):
    """Raised when a quickstart step fails."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_web3():
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except ImportError:
        from web3.middleware import geth_poa_middleware
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


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
    erc20_abi = [
        {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
         "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
        {"inputs": [{"name": "ownerAddress", "type": "address"}, {"name": "value", "type": "uint256"}],
         "name": "allocateTo", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        {"inputs": [], "name": "decimals",
         "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    ]

    token = w3.eth.contract(
        address=Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=erc20_abi,
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
# Step 2: Register Agent
# ---------------------------------------------------------------------------

def step2_register_agent() -> int:
    """Register agent on ERC-8004. Returns agent_id."""
    banner("Step 2", "Register Agent")

    from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

    wallet = EVMWalletProvider(password=WALLET_PASSWORD, private_key=PRIVATE_KEY)
    sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet", debug=True)

    agent_name = "quickstart-e2e-test"
    agent_uri = sdk.generate_agent_uri(
        name=agent_name,
        description="E2E test agent for quickstart validation",
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

    logger.info(f"Step 2 PASSED (Agent ID: {agent_id})")
    return agent_id


# ---------------------------------------------------------------------------
# Step 3: Start Agent Server (background)
# ---------------------------------------------------------------------------

async def start_agent_server() -> asyncio.Event:
    """Start the quickstart agent server in the background. Returns a ready event."""
    banner("Step 3", "Start Agent Server")

    from contextlib import asynccontextmanager
    from fastapi import FastAPI, Request
    from bnbagent.quickstart import APEXConfig, create_apex_state, create_apex_routes

    config = APEXConfig.from_env()
    state = create_apex_state(config)

    # Simple task processor
    def process_task(description: str) -> str:
        return f"E2E test response for: {description}"

    # Background polling
    async def poll_funded_jobs():
        logger.info(f"Polling for funded jobs every {POLL_INTERVAL}s...")
        while True:
            try:
                result = await state.job_ops.get_pending_jobs()
                if not result.get("success"):
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                jobs = result.get("jobs", [])
                for job in jobs:
                    job_id = job["jobId"]
                    description = job.get("description", "")
                    logger.info(f"[Agent] Processing job #{job_id}...")

                    verification = await state.job_ops.verify_job(job_id)
                    if not verification["valid"]:
                        logger.warning(f"[Agent] Job #{job_id} verification failed: {verification.get('error')}")
                        continue

                    response = process_task(description)
                    submission = await state.job_ops.submit_result(
                        job_id=job_id,
                        response_content=response,
                        metadata={"agent": "e2e-test"},
                    )
                    if submission.get("success"):
                        logger.info(f"[Agent] Job #{job_id} submitted! TX: {submission['txHash']}")
                    else:
                        logger.error(f"[Agent] Job #{job_id} failed: {submission.get('error')}")
            except Exception as e:
                logger.error(f"[Agent] Polling error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    server_ready = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(poll_funded_jobs())
        server_ready.set()
        yield
        task.cancel()

    app = FastAPI(title="E2E Test Agent", lifespan=lifespan)
    app.include_router(create_apex_routes(config=config, state=state))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

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
                resp = await client.get(f"http://127.0.0.1:{AGENT_PORT}/health", timeout=2)
                if resp.status_code == 200:
                    logger.info(f"Agent server running on port {AGENT_PORT}")
                    logger.info("Step 3 PASSED")
                    return server
        except Exception:
            continue

    raise StepError("Agent server failed to start within 15 seconds")


# ---------------------------------------------------------------------------
# Step 4: Create and Fund a Job
# ---------------------------------------------------------------------------

def step4_create_and_fund_job(agent_address: str) -> int:
    """Create, budget, approve, fund a job. Returns job_id."""
    banner("Step 4", "Create and Fund a Job")

    from web3 import Web3
    from bnbagent import APEXClient, APEXStatus
    from bnbagent.apex_client import get_default_expiry

    w3 = create_web3()
    apex = APEXClient(web3=w3, contract_address=ERC8183_ADDRESS, private_key=PRIVATE_KEY)
    account = w3.eth.account.from_key(PRIVATE_KEY)

    # 4a: Create job
    logger.info("4a: Creating job...")
    expiry = get_default_expiry()
    result = apex.create_job(
        provider=agent_address,
        evaluator=EVALUATOR_ADDRESS,
        expired_at=expiry,
        description="E2E test task: quickstart validation",
        hook=EVALUATOR_ADDRESS,
    )
    job_id = result["jobId"]
    logger.info(f"Created job #{job_id} (TX: {result['transactionHash'][:16]}...)")

    # 4b: Set budget
    budget = 1 * 10**18  # 1 U token
    logger.info(f"4b: Setting budget ({budget / 10**18} U)...")
    apex.set_budget(job_id, budget)

    # 4c: Approve BEP20
    logger.info("4c: Approving token spend...")
    erc20_abi = [{
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }]
    token = w3.eth.contract(
        address=Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=erc20_abi,
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

    # 4d: Fund job
    logger.info("4d: Funding job...")
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
    """Wait for the agent server to pick up and submit the job."""
    banner("Step 4e", "Wait for Agent Submission")

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
    """Fetch the deliverable from storage and verify its hash against on-chain."""
    banner("Step 4f", "Fetch and Verify Deliverable")

    import json
    from web3 import Web3
    from bnbagent import APEXClient

    w3 = create_web3()
    apex = APEXClient(web3=w3, contract_address=ERC8183_ADDRESS, private_key=PRIVATE_KEY)

    job = apex.get_job(job_id)
    deliverable_hash = job["deliverable"]
    logger.info(f"On-chain deliverable hash: 0x{deliverable_hash.hex()}")

    # Read from local storage
    storage_path = os.getenv("LOCAL_STORAGE_PATH", "./.agent-data")
    deliverable_file = os.path.join(storage_path, f"job-{job_id}.json")

    if not os.path.isfile(deliverable_file):
        raise StepError(f"Deliverable file not found: {deliverable_file}")

    with open(deliverable_file, "r") as f:
        deliverable_data = json.load(f)

    # Verify hash
    data_url = f"file://{os.path.abspath(deliverable_file)}"
    computed_hash = Web3.keccak(text=data_url)

    if computed_hash != deliverable_hash:
        raise StepError(
            f"Hash mismatch: on-chain=0x{deliverable_hash.hex()}, "
            f"computed=0x{computed_hash.hex()}"
        )
    logger.info("Hash verification: PASSED")

    # Display agent response
    response_content = deliverable_data.get("response", "")
    logger.info(f"Agent response: {response_content[:100]}...")

    logger.info("Step 4f PASSED")


# ---------------------------------------------------------------------------
# Step 5: Settle Job
# ---------------------------------------------------------------------------

def step5_settle_job(job_id: int) -> None:
    """Wait for liveness and settle the job via evaluator."""
    banner("Step 5", "Settle Job")

    from bnbagent import APEXClient, APEXStatus, APEXEvaluatorClient

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

    erc20_abi = [
        {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
         "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "decimals",
         "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    ]
    token = w3.eth.contract(
        address=W3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=erc20_abi,
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
  Quickstart E2E Test
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

        # Step 2
        agent_id = step2_register_agent()
        results["step2"] = "PASSED"

        # Step 3 — start server in background
        server = await start_agent_server()
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

"""
Demo Flow — APEX Protocol Client.

Built with bnbagent-sdk.

A client script that drives the full APEX protocol lifecycle:
  Step 0: Discover provider from ERC-8004 (optional, if AGENT_ID is set)
  Step 1: Negotiate with News Agent
  Step 2: Create ERC-8183 Job on-chain
  Step 3: Set Budget
  Step 4: Approve BEP20 & Fund Escrow
  Step 5: Wait for News Agent to deliver
  Step 6: Fetch deliverable & generate bilingual newsletter
  Step 7: Monitor Assertion (APEX Evaluator)
  Step 8: Final Status & Money Flow

Usage:
    cd agents
    uv run python -m demo.demo_flow "What are the latest BNB Chain news?"

    # With ERC-8004 discovery (set AGENT_ID in .env.editor)
    uv run python -m demo.demo_flow --discover "query..."

Environment (demo/.env.editor):
    RPC_URL, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS, PRIVATE_KEY,
    AGENT_SERVER_ADDRESS, AGENT_SERVER_URL=http://localhost:8003,
    PAYMENT_TOKEN_ADDRESS, OPENROUTER_API_KEY
    AGENT_ID (optional) — For ERC-8004 discovery
    WALLET_PASSWORD (optional) — For ERC-8004 SDK
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from web3 import Web3

# Load env from demo root directory (.env next to scripts/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# SDK imports — use public API paths (bnbagent.* and bnbagent.apex.*)
from bnbagent import APEXClient, APEXStatus
from bnbagent.apex import APEXEvaluatorClient, get_default_expiry
from bnbagent.config import resolve_network
from bnbagent.core import NonceManager, load_erc20_abi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_nc = resolve_network()
RPC_URL = os.environ.get("RPC_URL") or _nc.rpc_url
ERC8183_ADDRESS = os.environ.get("ERC8183_ADDRESS") or _nc.erc8183_contract
APEX_EVALUATOR_ADDRESS = os.environ.get("APEX_EVALUATOR_ADDRESS") or _nc.apex_evaluator
PRIVATE_KEY = os.environ["PRIVATE_KEY"]  # Required — no default
AGENT_B_ADDRESS = os.getenv("AGENT_SERVER_ADDRESS", "")
AGENT_B_URL = os.getenv("AGENT_SERVER_URL", "http://localhost:8003")
PAYMENT_TOKEN_ADDRESS = os.environ.get("PAYMENT_TOKEN_ADDRESS") or _nc.payment_token
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash")
AGENT_ID = os.getenv("AGENT_ID", "")
WALLET_PASSWORD = os.getenv("WALLET_PASSWORD", "demo-password")
DISPUTE_PRIVATE_KEY = os.getenv("DISPUTE_PRIVATE_KEY", "")

BUDGET = 1 * 10**18  # 1 U token

# UMA OOv3 contract address on BSC testnet
OOV3_ADDRESS = "0xFc5bb3e475cc9264760Cf33b1e9ea7B87942C709"

# OOv3 ABI for dispute
OOV3_ABI = [
    {
        "name": "disputeAssertion",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "assertionId", "type": "bytes32"},
            {"name": "disputer", "type": "address"},
        ],
        "outputs": [],
    },
    {
        "name": "getAssertion",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "assertionId", "type": "bytes32"}],
        "outputs": [{
            "name": "",
            "type": "tuple",
            "components": [
                {"name": "escalationManagerSettings", "type": "tuple", "components": [
                    {"name": "arbitrateViaEscalationManager", "type": "bool"},
                    {"name": "discardOracle", "type": "bool"},
                    {"name": "validateDisputers", "type": "bool"},
                    {"name": "assertingCaller", "type": "address"},
                    {"name": "escalationManager", "type": "address"},
                ]},
                {"name": "asserter", "type": "address"},
                {"name": "assertionTime", "type": "uint64"},
                {"name": "settled", "type": "bool"},
                {"name": "currency", "type": "address"},
                {"name": "expirationTime", "type": "uint64"},
                {"name": "settlementResolution", "type": "bool"},
                {"name": "domainId", "type": "bytes32"},
                {"name": "identifier", "type": "bytes32"},
                {"name": "bond", "type": "uint256"},
                {"name": "callbackRecipient", "type": "address"},
                {"name": "disputer", "type": "address"},
            ]
        }],
    },
    {
        "name": "getMinimumBond",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "currency", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "cachedOracle",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
]

# MockOracle ABI for testnet resolution
MOCK_ORACLE_ABI = [
    {
        "name": "pushPriceByRequestId",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "requestId", "type": "bytes32"},
            {"name": "price", "type": "int256"},
        ],
        "outputs": [],
    },
]

# AssertionDisputed event signature for finding REQUEST_ID
ASSERTION_DISPUTED_EVENT = {
    "name": "AssertionDisputed",
    "type": "event",
    "inputs": [
        {"name": "assertionId", "type": "bytes32", "indexed": True},
        {"name": "caller", "type": "address", "indexed": True},
        {"name": "disputer", "type": "address", "indexed": True},
    ],
}


# ---------------------------------------------------------------------------
# ERC-8004 Discovery
# ---------------------------------------------------------------------------

def discover_agent_from_8004(agent_id: str) -> tuple[str, str]:
    """
    Discover agent from ERC-8004 registry by agent ID.

    Returns:
        Tuple of (agent_address, agent_url)
    """
    from bnbagent import ERC8004Agent, EVMWalletProvider

    wallet = EVMWalletProvider(
        password=WALLET_PASSWORD,
        private_key=PRIVATE_KEY,
    )

    sdk = ERC8004Agent(
        network="bsc-testnet",
        wallet_provider=wallet,
        debug=False,
    )

    info = sdk.get_agent_info(agent_id=int(agent_id))
    agent_address = info["owner"]

    # Parse agent URI using SDK (includes SSRF protection)
    agent_uri = info.get("agentURI", "")
    agent_url = "http://localhost:8003"

    parsed = sdk.parse_agent_uri(agent_uri)
    if parsed:
        services = parsed.get("services", [])
        for svc in services:
            endpoint = svc.get("endpoint", "")
            if endpoint:
                if endpoint.endswith("/status"):
                    agent_url = endpoint.rsplit("/status", 1)[0]
                else:
                    agent_url = endpoint
                break

    return agent_address, agent_url


def get_balance(w3: Web3, token_address: str, account: str) -> int:
    """Get BEP20 token balance."""
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI,
    )
    return token.functions.balanceOf(Web3.to_checksum_address(account)).call()


def show_balances(
    w3: Web3,
    token_address: str,
    client: str,
    provider: str,
    initial_client: int = None,
    initial_provider: int = None,
    label: str = "Balance",
):
    """Display current balances with optional diff from initial."""
    client_bal = get_balance(w3, token_address, client)
    provider_bal = get_balance(w3, token_address, provider)

    print(f"\n  ── {label} ──")

    if initial_client is not None:
        diff_c = client_bal - initial_client
        diff_p = provider_bal - initial_provider
        print(f"  Client:   {client_bal / 10**18:>10.4f} U  ({diff_c / 10**18:+.4f})")
        print(f"  Provider: {provider_bal / 10**18:>10.4f} U  ({diff_p / 10**18:+.4f})")
    else:
        print(f"  Client:   {client_bal / 10**18:>10.4f} U")
        print(f"  Provider: {provider_bal / 10**18:>10.4f} U")

    return client_bal, provider_bal

# Minimal BEP20 ABI for approve + allocateTo (testnet faucet)
ERC20_ABI = load_erc20_abi()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def banner(step: int, total: int, title: str):
    print(f"\n{'='*60}")
    print(f"  [Step {step}/{total}] {title}")
    print(f"{'='*60}")


def tx_link(tx_hash: str) -> str:
    return f"  TX: {tx_hash}"


def send_tx(w3: Web3, fn, private_key: str, account: str, gas: int = 500_000):
    """Build, sign, send a transaction and wait for receipt."""
    nonce_mgr = NonceManager.for_account(w3, account)
    nonce = nonce_mgr.get_nonce()
    tx = fn.build_transaction({
        "from": account,
        "nonce": nonce,
        "gas": gas,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction reverted! TX: {tx_hash.hex()}")
    return receipt


async def dispute_assertion(
    w3: Web3,
    oov3_address: str,
    assertion_id: bytes,
    disputer: str,
    private_key: str,
    bond_amount: int,
) -> dict:
    """Dispute a UMA assertion during the challenge period."""
    oov3 = w3.eth.contract(
        address=Web3.to_checksum_address(oov3_address),
        abi=OOV3_ABI,
    )
    token = w3.eth.contract(
        address=Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS),
        abi=ERC20_ABI,
    )

    # Approve bond — NonceManager auto-increments
    approve_fn = token.functions.approve(
        Web3.to_checksum_address(oov3_address),
        bond_amount,
    )
    send_tx(w3, approve_fn, private_key, disputer)

    # Dispute — gets next nonce automatically
    dispute_fn = oov3.functions.disputeAssertion(
        assertion_id,
        Web3.to_checksum_address(disputer),
    )
    receipt = send_tx(w3, dispute_fn, private_key, disputer, gas=800_000)
    return {"transactionHash": receipt["transactionHash"].hex()}


async def resolve_dispute_with_mock_oracle(
    w3: Web3,
    assertion_id: bytes,
    resolve_true: bool,
    private_key: str,
    account: str,
    dispute_tx_hash: str = None,
) -> dict:
    """
    Resolve a disputed assertion using MockOracle (testnet only).

    Steps:
    1. Use the dispute TX hash (passed in, or search for it)
    2. Extract REQUEST_ID from the dispute TX receipt
    3. Call MockOracle.pushPriceByRequestId(requestId, price)
    4. Return success for caller to then settleJob

    On mainnet, this would go through UMA's DVM voting process.
    """
    oov3 = w3.eth.contract(
        address=Web3.to_checksum_address(OOV3_ADDRESS),
        abi=OOV3_ABI,
    )

    # Step 1: Get dispute TX hash
    if dispute_tx_hash:
        # Convert hex string to bytes if needed
        if isinstance(dispute_tx_hash, str):
            dispute_tx_hash = bytes.fromhex(dispute_tx_hash.replace("0x", ""))
        print(f"    Using dispute TX: {dispute_tx_hash.hex()}")
    else:
        # Fallback: search for AssertionDisputed event
        print("    Finding AssertionDisputed event...")
        latest_block = w3.eth.block_number
        batch_size = 4900
        max_batches = 20
        event_sig = "0x" + w3.keccak(text="AssertionDisputed(bytes32,address,address)").hex()
        assertion_topic = "0x" + assertion_id.hex()

        for i in range(max_batches):
            from_block = max(0, latest_block - (i + 1) * batch_size)
            to_block = latest_block - i * batch_size

            try:
                logs = w3.eth.get_logs({
                    "address": Web3.to_checksum_address(OOV3_ADDRESS),
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "topics": [event_sig, assertion_topic],
                })

                if logs:
                    dispute_tx_hash = logs[0]["transactionHash"]
                    print(f"    Found dispute TX: {dispute_tx_hash.hex()}")
                    break
            except Exception:
                pass

            if from_block == 0:
                break

        if not dispute_tx_hash:
            return {"success": False, "error": "AssertionDisputed event not found"}

    # Step 2: Get REQUEST_ID from dispute TX receipt
    print("    Extracting REQUEST_ID...")
    receipt = w3.eth.get_transaction_receipt(dispute_tx_hash)

    # Get oracle address from OOv3
    try:
        oracle_address = oov3.functions.cachedOracle().call()
    except Exception:
        # Fallback - search for oracle in logs
        oracle_address = None

    # Find the oracle log with REQUEST_ID (topic[3])
    request_id = None
    oracle_log = None

    known_addresses = [
        OOV3_ADDRESS.lower(),
        ERC8183_ADDRESS.lower(),
        APEX_EVALUATOR_ADDRESS.lower(),
    ]

    for log in receipt["logs"]:
        if len(log["topics"]) >= 4:
            log_addr = log["address"].lower()
            if oracle_address and log_addr == oracle_address.lower():
                oracle_log = log
                break
            elif log_addr not in known_addresses:
                oracle_log = log

    if oracle_log and len(oracle_log["topics"]) >= 4:
        request_id = oracle_log["topics"][3]
        oracle_address = oracle_log["address"]
        print(f"    REQUEST_ID: {request_id.hex()}")
        print(f"    Oracle: {oracle_address}")
    else:
        return {"success": False, "error": "REQUEST_ID not found in dispute TX"}

    # Step 3: Push price to MockOracle
    print(f"    Pushing price to MockOracle...")
    price = 10**18 if resolve_true else 0

    mock_oracle = w3.eth.contract(
        address=Web3.to_checksum_address(oracle_address),
        abi=MOCK_ORACLE_ABI,
    )

    push_fn = mock_oracle.functions.pushPriceByRequestId(request_id, price)

    try:
        receipt = send_tx(w3, push_fn, private_key, account, gas=500_000)
        return {"success": True, "transactionHash": receipt["transactionHash"].hex()}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def call_llm(prompt: str, system: str = "", max_retries: int = 3) -> str:
    """Call OpenRouter chat completion API with retry on transient errors."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(max_retries):
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": messages,
                    "max_tokens": 4096,
                },
            )
            if resp.status_code >= 500 and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  OpenRouter returned {resp.status_code}, retrying in {wait}s (attempt {attempt+1}/{max_retries})...")
                await asyncio.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  OpenRouter error {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Main flow
#
# Steps 2-5 follow bnbagent-sdk/examples/client_workflow.py exactly:
#   APEXClient.create_job() → set_budget() → fund() → poll get_job()
# Steps 1, 6-8 are demo-specific additions.
# ---------------------------------------------------------------------------

async def main():
    global AGENT_B_ADDRESS, AGENT_B_URL

    # Parse arguments
    args = sys.argv[1:]
    use_discovery = "--discover" in args
    if use_discovery:
        args.remove("--discover")

    task_description = " ".join(args) if args else (
        "What are the latest developments on BNB Chain this week?"
    )
    total_steps = 8

    # ── Web3 + SDK setup ──

    from bnbagent.core import create_web3
    w3 = create_web3(RPC_URL)

    account = w3.eth.account.from_key(PRIVATE_KEY).address

    # Third-party disputer (optional - if not set, use client's key)
    if DISPUTE_PRIVATE_KEY:
        disputer_address = w3.eth.account.from_key(DISPUTE_PRIVATE_KEY).address
        disputer_key = DISPUTE_PRIVATE_KEY
    else:
        disputer_address = account
        disputer_key = PRIVATE_KEY

    # ══════════════════════════════════════════════════════════════════════
    # Step 0: Discover from ERC-8004 (optional)
    # ══════════════════════════════════════════════════════════════════════
    if use_discovery or (AGENT_ID and not AGENT_B_ADDRESS):
        if not AGENT_ID:
            print("Error: --discover requires AGENT_ID in .env.editor")
            sys.exit(1)

        banner(0, total_steps, "Discover Agent from ERC-8004")
        print(f"  Looking up Agent ID: {AGENT_ID}")

        try:
            AGENT_B_ADDRESS, AGENT_B_URL = discover_agent_from_8004(AGENT_ID)
            print(f"  Found!")
            print(f"  Agent Address: {AGENT_B_ADDRESS}")
            print(f"  Agent URL:     {AGENT_B_URL}")
        except Exception as e:
            print(f"  Discovery failed: {e}")
            print("  Falling back to env variables...")
            if not AGENT_B_ADDRESS:
                print("Error: AGENT_SERVER_ADDRESS required")
                sys.exit(1)

    if not AGENT_B_ADDRESS:
        print("Error: AGENT_SERVER_ADDRESS required in .env or use --discover")
        sys.exit(1)

    print(f"""
{'#'*60}
  ERC-8183 Demo — Client
{'#'*60}
  Query: {task_description}
  News Agent URL: {AGENT_B_URL}
  Budget: {BUDGET / 10**18} U tokens
""")

    # Initialize SDK clients (same as client_workflow.py)
    apex = APEXClient(
        web3=w3,
        contract_address=ERC8183_ADDRESS,
        private_key=PRIVATE_KEY,
    )
    evaluator = APEXEvaluatorClient(
        web3=w3,
        contract_address=APEX_EVALUATOR_ADDRESS,
        private_key=PRIVATE_KEY,
    )
    token = w3.eth.contract(
        address=Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS), abi=ERC20_ABI
    )

    print(f"  Client:    {account}")
    print(f"  Provider:  {AGENT_B_ADDRESS}")
    print(f"  ERC-8183 Contract:   {ERC8183_ADDRESS}")
    print(f"  Evaluator:           {APEX_EVALUATOR_ADDRESS}")

    # ── Initial Balance Check ──
    initial_client_balance = get_balance(w3, PAYMENT_TOKEN_ADDRESS, account)
    initial_provider_balance = get_balance(w3, PAYMENT_TOKEN_ADDRESS, AGENT_B_ADDRESS)

    # Track balances for showing changes
    last_client_bal = initial_client_balance
    last_provider_bal = initial_provider_balance

    print(f"""
  ┌────────────────────────────────────────┐
  │  💰 Initial Balances                   │
  │  Client:   {initial_client_balance / 10**18:>10.4f} U              │
  │  Provider: {initial_provider_balance / 10**18:>10.4f} U              │
  └────────────────────────────────────────┘
""")

    # ══════════════════════════════════════════════════════════════════════
    # Step 1: Negotiate (demo addition — not in client_workflow.py)
    #
    # The provider agent exposes POST /apex/negotiate via SDK's
    # create_apex_app(). We call it to agree on price before creating a job.
    # ══════════════════════════════════════════════════════════════════════
    banner(1, total_steps, "Negotiate with News Agent")

    negotiate_payload = {
        "task_description": task_description,
        "terms": {
            "service_type": "blockchain-news",
            "deliverables": "Structured English news summary with sources",
            "quality_standards": "Accurate, well-sourced, covers at least 5 news items",
        },
    }
    print(f"  POST {AGENT_B_URL}/apex/negotiate")
    print(f"  Payload: {json.dumps(negotiate_payload, indent=4)}")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{AGENT_B_URL}/apex/negotiate", json=negotiate_payload)
        resp.raise_for_status()
        neg_result = resp.json()

    accepted = neg_result.get("response", {}).get("accepted", False)
    price_wei = neg_result.get("response", {}).get("terms", {}).get("price", "0")
    price_human = int(price_wei) / 10**18

    print(f"\n  Response: accepted={accepted}, price={price_human} U tokens")
    if not accepted:
        reason = neg_result.get("response", {}).get("reason", "unknown")
        print(f"  Rejected: {reason}")
        sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════
    # Step 2: Create Job (same as client_workflow.py Step 1)
    # ══════════════════════════════════════════════════════════════════════
    banner(2, total_steps, "Create ERC-8183 Job on BNB Chain")

    expiry = get_default_expiry()
    print(f"  Provider:    {AGENT_B_ADDRESS}")
    print(f"  Evaluator:   {APEX_EVALUATOR_ADDRESS}")
    print(f"  Hook:        {APEX_EVALUATOR_ADDRESS} (auto-assertion)")
    print(f"  Expiry:      {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry))}")
    print(f"  Description: {task_description[:80]}...  (structured JSON with negotiation terms)")

    from bnbagent.apex.negotiation import build_job_description

    description = build_job_description(neg_result)
    result = apex.create_job(
        provider=AGENT_B_ADDRESS,
        evaluator=APEX_EVALUATOR_ADDRESS,
        expired_at=expiry,
        description=description,
        hook=APEX_EVALUATOR_ADDRESS,
    )
    job_id = result["jobId"]
    print(f"\n  Job #{job_id} created!")
    print(tx_link(result["transactionHash"]))

    # Wait for transaction to be mined before next tx
    print("  Waiting for confirmation...")
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════
    # Step 3: Set Budget (same as client_workflow.py Step 2)
    # ══════════════════════════════════════════════════════════════════════
    banner(3, total_steps, f"Set Budget: {BUDGET / 10**18} U tokens")

    result = apex.set_budget(job_id, BUDGET)
    print(f"  Budget set to {BUDGET / 10**18} U tokens for Job #{job_id}")
    print(tx_link(result["transactionHash"]))
    print("  Waiting for confirmation...")
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════
    # Step 4: Approve & Fund (same as client_workflow.py Step 3)
    #
    # Note: client_workflow.py assumes manual approval. This demo
    # automates it including testnet token minting for convenience.
    # ══════════════════════════════════════════════════════════════════════
    banner(4, total_steps, "Approve BEP20 & Fund Escrow")

    # Check balance; mint if needed (testnet only)
    balance = token.functions.balanceOf(account).call()
    print(f"  Current U balance: {balance / 10**18}")
    if balance < BUDGET:
        mint_amount = BUDGET - balance + 10 * 10**18  # extra buffer
        print(f"  Minting {mint_amount / 10**18} U tokens (testnet allocateTo)...")
        receipt = send_tx(
            w3,
            token.functions.allocateTo(account, mint_amount),
            PRIVATE_KEY,
            account,
        )
        print(f"  Minted! {tx_link(receipt['transactionHash'].hex())}")
        time.sleep(3)
        balance = token.functions.balanceOf(account).call()
        print(f"  New balance: {balance / 10**18}")

    # Approve ERC-8183 contract
    print(f"\n  Approving ERC-8183 contract to spend {BUDGET / 10**18} U...")
    receipt = send_tx(
        w3,
        token.functions.approve(Web3.to_checksum_address(ERC8183_ADDRESS), BUDGET),
        PRIVATE_KEY,
        account,
    )
    print(f"  Approved! {tx_link(receipt['transactionHash'].hex())}")
    time.sleep(3)

    # Fund (same as client_workflow.py)
    print(f"\n  Funding Job #{job_id} with {BUDGET / 10**18} U...")
    result = apex.fund(job_id, BUDGET)
    print(f"  Funded! Job #{job_id} status: FUNDED")
    print(tx_link(result["transactionHash"]))
    time.sleep(3)

    # Show balance after funding (money moved to escrow)
    cur_client, cur_provider = show_balances(
        w3, PAYMENT_TOKEN_ADDRESS, account, AGENT_B_ADDRESS,
        initial_client_balance, initial_provider_balance,
        "💰 After Funding (→ Escrow)"
    )
    print(f"  📝 {BUDGET / 10**18} U locked in ERC-8183 escrow contract")
    last_client_bal, last_provider_bal = cur_client, cur_provider

    # ══════════════════════════════════════════════════════════════════════
    # Step 5: Trigger Execution & Wait for Agent
    # ══════════════════════════════════════════════════════════════════════
    banner(5, total_steps, "Trigger /job/execute & Wait for Delivery")

    execute_url = f"{AGENT_B_URL}/apex/job/execute"
    print(f"  POST {execute_url}")
    print(f"  Payload: {json.dumps({'job_id': job_id, 'timeout': 30})}")
    async with httpx.AsyncClient(timeout=60) as http_client:
        try:
            resp = await http_client.post(
                execute_url, json={"job_id": job_id, "timeout": 30},
            )
            print(f"  Response: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Job completed immediately! TX: {data.get('txHash', 'N/A')}")
            elif resp.status_code == 202:
                print("  Job accepted, processing in background. Polling...")
            else:
                print(f"  Unexpected: {resp.text[:200]}")
        except Exception as e:
            print(f"  /job/execute failed ({e}), falling back to polling...")

    print("  Polling job status every 10s...")
    while True:
        job = apex.get_job(job_id)
        status = APEXStatus(job["status"])

        if status == APEXStatus.SUBMITTED:
            print(f"\n  ✓ Status: SUBMITTED!")

            # Show balance (still in escrow, waiting for UMA)
            cur_client, cur_provider = show_balances(
                w3, PAYMENT_TOKEN_ADDRESS, account, AGENT_B_ADDRESS,
                initial_client_balance, initial_provider_balance,
                "💰 After Submit (still in escrow)"
            )
            print(f"  📝 Funds still locked pending UMA challenge period")
            last_client_bal, last_provider_bal = cur_client, cur_provider
            break
        elif status == APEXStatus.COMPLETED:
            print(f"\n  ✓ Status: COMPLETED (already settled)")
            break
        elif status in (APEXStatus.REJECTED, APEXStatus.EXPIRED):
            print(f"\n  Status: {status.name} — aborting.")
            sys.exit(1)
        else:
            print(f"  Status: {status.name} — waiting...")
            await asyncio.sleep(10)

    # ══════════════════════════════════════════════════════════════════════
    # Step 6: Fetch deliverable & generate newsletter (demo addition)
    # ══════════════════════════════════════════════════════════════════════
    banner(6, total_steps, "Fetch Deliverable & Generate Newsletter")

    # Try to get deliverable content — three strategies:
    #   1. IPFS via Pinata (if STORAGE_API_KEY or PINATA_JWT configured)
    #   2. Reporter agent's /search endpoint (direct fallback)
    #   3. On-chain job description as last resort
    news_content = ""
    pinata_gateway = os.getenv("STORAGE_GATEWAY_URL", "https://gateway.pinata.cloud/ipfs/")

    # Strategy 1: IPFS via Pinata
    pinata_jwt = os.getenv("STORAGE_API_KEY") or os.getenv("PINATA_JWT", "")
    if pinata_jwt:
        print("  Fetching deliverable from IPFS...")
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    "https://api.pinata.cloud/data/pinList",
                    params={"metadata[name]": f"job-{job_id}", "pageLimit": 1},
                    headers={"Authorization": f"Bearer {pinata_jwt}"},
                )
                if resp.status_code == 200:
                    pins = resp.json().get("rows", [])
                    if pins:
                        ipfs_hash = pins[0]["ipfs_pin_hash"]
                        data_url = f"{pinata_gateway}{ipfs_hash}"
                        print(f"  IPFS URL: {data_url}")

                        content_resp = await client.get(data_url)
                        if content_resp.status_code == 200:
                            ipfs_data = content_resp.json()
                            news_content = ipfs_data.get("response", "")
                            print(f"  Deliverable fetched from IPFS ({len(news_content)} chars)")
            except Exception as e:
                print(f"  IPFS fetch warning: {e}")

    # Strategy 2: Query reporter agent's /search endpoint directly
    if not news_content:
        print("  IPFS unavailable, querying reporter agent directly...")
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(
                    f"{AGENT_B_URL}/search",
                    json={"query": task_description, "max_results": 10},
                )
                if resp.status_code == 200:
                    search_data = resp.json()
                    items = search_data.get("results", [])
                    if items:
                        # Format structured results into a readable report
                        parts = [f"# Blockchain News: {task_description}\n"]
                        for i, item in enumerate(items, 1):
                            parts.append(f"## {i}. {item.get('title', 'Untitled')}")
                            if item.get("source") or item.get("date"):
                                parts.append(f"*{item.get('source', '')}* | {item.get('date', '')}")
                            parts.append(item.get("body", ""))
                            if item.get("url"):
                                parts.append(f"[Read more]({item['url']})")
                            parts.append("")
                        news_content = "\n\n".join(parts)
                        print(f"  Deliverable fetched from reporter ({len(news_content)} chars, {len(items)} items)")
                    else:
                        print(f"  Reporter returned no results")
                else:
                    print(f"  Reporter /search returned {resp.status_code}")
            except Exception as e:
                print(f"  Reporter query failed: {e}")

    # Strategy 3: Use on-chain job description as context
    if not news_content:
        print("  WARNING: Could not fetch deliverable content, using job description")
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(f"{AGENT_B_URL}/apex/job/{job_id}")
                if resp.status_code == 200:
                    job_data = resp.json()
                    news_content = job_data.get("description", task_description)
            except Exception:
                news_content = task_description

    # Generate newsletter
    if not OPENROUTER_API_KEY:
        print("\n  Skipping newsletter generation (OPENROUTER_API_KEY not set)")
        print("  Set OPENROUTER_API_KEY in .env to enable this step")
    else:
        print("\n  Generating newsletter with LLM...")

        newsletter_prompt = f"""You are a newsletter editor. Take the following blockchain news summary and create a concise newsletter.

News:
{news_content}

Create a newsletter with:
1. A catchy newsletter title
2. Each news item summarized clearly
3. Key takeaways section
4. Keep it professional, concise, and suitable for a crypto developer audience

Format it nicely with markdown headers and bullet points."""

        newsletter = await call_llm(
            newsletter_prompt,
            system="You are a professional newsletter editor for the blockchain industry.",
        )

        print(f"\n{'─'*60}")
        print("  GENERATED NEWSLETTER")
        print(f"{'─'*60}")
        print(newsletter)
        print(f"{'─'*60}")

    # ══════════════════════════════════════════════════════════════════════
    # Step 7: UMA Challenge Period — Wait, Dispute, or Settle
    # ══════════════════════════════════════════════════════════════════════
    banner(7, total_steps, "UMA Challenge Period")

    info = None
    try:
        info = evaluator.get_assertion_info(job_id)
    except Exception as e:
        print(f"  Could not get assertion info: {e}")

    if not info or not info.initiated:
        print("  Assertion not found. Skipping UMA flow...")
    else:
        assertion_hex = "0x" + info.assertion_id.hex()
        remaining = max(0, info.liveness_end - int(time.time()))

        print(f"  Assertion ID:      {assertion_hex}")
        print(f"  Disputed:          {info.disputed}")
        print(f"  Liveness ends:     {time.strftime('%H:%M:%S', time.localtime(info.liveness_end))}")
        print(f"  Time remaining:    {remaining}s ({remaining // 60}m {remaining % 60}s)")
        print(f"  Settleable:        {info.settleable}")

        print(f"""
  ┌─────────────────────────────────────────────────────────┐
  │  Choose an action:                                       │
  │                                                          │
  │  [1] Wait for liveness to expire, then settle           │
  │      (Auto-wait {remaining}s, then complete payment)     │
  │                                                          │
  │  [2] Demonstrate DISPUTE flow                           │
  │      (Dispute → MockOracle resolve → settle)            │
  │                                                          │
  │  [3] Skip (manual settlement later)                     │
  └─────────────────────────────────────────────────────────┘
""")
        choice = await asyncio.to_thread(input, "  Enter choice [1/2/3]: ")
        choice = choice.strip()

        if choice == "1":
            # ── Option 1: Wait and Settle ──
            print(f"\n  Waiting for liveness period to end...")

            while True:
                try:
                    info = evaluator.get_assertion_info(job_id)
                    if info.settleable:
                        print(f"\n  ✓ Liveness period ended! Settleable now.")
                        break
                    if info.disputed:
                        print(f"\n  ! Assertion was disputed by someone else.")
                        break

                    remaining = max(0, info.liveness_end - int(time.time()))
                    if remaining <= 0:
                        break

                    wait_time = min(30, remaining + 5)
                    print(f"    Remaining: {remaining}s — checking again in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    print(f"    Error checking status: {e}")
                    await asyncio.sleep(10)

            # Settle
            print("  Settling job...")
            try:
                result = evaluator.settle_job(job_id)
                print(f"  ✓ Settled! TX: {result['transactionHash']}")
                time.sleep(3)

                # Show balance after settlement
                cur_client, cur_provider = show_balances(
                    w3, PAYMENT_TOKEN_ADDRESS, account, AGENT_B_ADDRESS,
                    initial_client_balance, initial_provider_balance,
                    "💰 After Settlement (escrow → provider)"
                )
                print(f"  ✅ Provider received {BUDGET / 10**18} U from escrow!")
            except Exception as e:
                print(f"  Settlement error: {e}")
                print("  (Job may have already been settled or disputed)")

        elif choice == "2":
            # ── Option 2: Demonstrate Dispute Flow ──
            print(f"\n  ── Dispute Flow Demo ──")

            # Get bond amount
            oov3 = w3.eth.contract(
                address=Web3.to_checksum_address(OOV3_ADDRESS),
                abi=OOV3_ABI,
            )
            bond = oov3.functions.getMinimumBond(
                Web3.to_checksum_address(PAYMENT_TOKEN_ADDRESS)
            ).call()
            print(f"  Bond required: {bond / 10**18} U")

            # Check disputer's balance (third-party or client)
            print(f"  Disputer: {disputer_address[:10]}...{disputer_address[-6:]}")
            disputer_balance = token.functions.balanceOf(disputer_address).call()
            if disputer_balance < bond:
                print(f"  ⚠ Disputer insufficient balance ({disputer_balance / 10**18} U). Minting...")
                mint_amount = bond + 10 * 10**18
                receipt = send_tx(
                    w3,
                    token.functions.allocateTo(disputer_address, mint_amount),
                    disputer_key,
                    disputer_address,
                )
                print(f"    Minted! TX: {receipt['transactionHash'].hex()}")
                time.sleep(3)

            # Dispute (using third-party disputer if configured)
            print(f"\n  Disputing assertion...")
            try:
                result = await dispute_assertion(
                    w3=w3,
                    oov3_address=OOV3_ADDRESS,
                    assertion_id=info.assertion_id,
                    disputer=disputer_address,
                    private_key=disputer_key,
                    bond_amount=bond,
                )
                print(f"  ✓ Disputed! TX: {result['transactionHash']}")
                time.sleep(3)

                # Show balance after dispute (bond paid by disputer)
                cur_client, cur_provider = show_balances(
                    w3, PAYMENT_TOKEN_ADDRESS, account, AGENT_B_ADDRESS,
                    initial_client_balance, initial_provider_balance,
                    "💰 After Dispute (client unchanged, bond paid by disputer)"
                )
                print(f"  📝 Dispute bond of {bond / 10**18} U paid by disputer to UMA OOv3")
            except Exception as e:
                print(f"  Dispute failed: {e}")
                print("  (Challenge period may have ended)")

            # Re-check
            try:
                info = evaluator.get_assertion_info(job_id)
                print(f"\n  Assertion disputed: {info.disputed}")
            except Exception:
                pass

            if info and info.disputed:
                print(f"""
  ── MockOracle Resolution (Testnet Only) ──

  On testnet, we use MockOracle to simulate DVM voting.
  Choose resolution:

    [T] Resolve TRUE  — Provider's work is APPROVED, gets paid
    [F] Resolve FALSE — Provider's work is REJECTED, client refunded
""")
                resolve_choice = await asyncio.to_thread(
                    input, "  Enter [T/F]: "
                )
                resolve_true = resolve_choice.strip().upper() == "T"

                print(f"\n  Resolving as {'TRUE (approve)' if resolve_true else 'FALSE (reject)'}...")

                # Auto-resolve using MockOracle
                resolve_result = await resolve_dispute_with_mock_oracle(
                    w3=w3,
                    assertion_id=info.assertion_id,
                    resolve_true=resolve_true,
                    private_key=PRIVATE_KEY,
                    account=account,
                    dispute_tx_hash=result.get("transactionHash"),
                )

                if resolve_result.get("success"):
                    print(f"  ✓ Price pushed to MockOracle! TX: {resolve_result['transactionHash']}")
                    time.sleep(5)

                    # Now settle the job
                    print("  Settling job...")
                    try:
                        result = evaluator.settle_job(job_id)
                        print(f"  ✓ Settled! TX: {result['transactionHash']}")
                        time.sleep(3)

                        # Show balance after settlement
                        cur_client, cur_provider = show_balances(
                            w3, PAYMENT_TOKEN_ADDRESS, account, AGENT_B_ADDRESS,
                            initial_client_balance, initial_provider_balance,
                            "💰 After Dispute Settlement"
                        )
                        if resolve_true:
                            print(f"  ✅ Provider received {BUDGET / 10**18} U (dispute resolved TRUE)")
                        else:
                            print(f"  ✅ Client refunded {BUDGET / 10**18} U (dispute resolved FALSE)")
                    except Exception as e:
                        print(f"  Settlement note: {e}")
                        print("  (Job may already be settled)")
                else:
                    error = resolve_result.get("error", "Unknown error")
                    print(f"  ⚠ Auto-resolve failed: {error}")
                    print(f"""
  To resolve manually, run:

    cd scripts
    JOB_ID={job_id} npm run resolve-dispute -- {job_id} {'true' if resolve_true else 'false'}
""")

        else:
            # ── Option 3: Skip ──
            print(f"""
  Skipped. To complete manually later:

  # Wait for liveness, then settle:
  cd scripts && JOB_ID={job_id} npm run settle-job

  # Or dispute:
  cd scripts && JOB_ID={job_id} npm run dispute-job
""")

    # ══════════════════════════════════════════════════════════════════════
    # Step 8: Final Status & Money Flow
    # ══════════════════════════════════════════════════════════════════════
    banner(8, total_steps, "Final Status & Money Flow")

    # Final job status
    job = apex.get_job(job_id)
    final_status = APEXStatus(job["status"])
    print(f"\n  Job #{job_id} final status: {final_status.name}")

    if final_status == APEXStatus.COMPLETED:
        print(f"  ✅ Provider (News Agent) received {BUDGET / 10**18} U tokens!")
    elif final_status == APEXStatus.REJECTED:
        print(f"  ✅ Job was rejected — client refunded.")
        print(f"  Dispute resolved FALSE: escrow returned to client + client won dispute bond.")
    else:
        print(f"  ⚠ Status: {final_status.name} — settlement has NOT completed yet.")
        print(f"  The money flow below reflects the CURRENT state, not the final outcome.")
        print(f"  To complete settlement, run:")
        print(f"    cd scripts && JOB_ID={job_id} npm run settle-job")

    # ── Final Balance Check & Money Flow ──
    final_client_balance = get_balance(w3, PAYMENT_TOKEN_ADDRESS, account)
    final_provider_balance = get_balance(w3, PAYMENT_TOKEN_ADDRESS, AGENT_B_ADDRESS)

    client_diff = final_client_balance - initial_client_balance
    provider_diff = final_provider_balance - initial_provider_balance

    print(f"""
{'─'*60}
  MONEY FLOW SUMMARY
{'─'*60}
  Client (you):
    Before: {initial_client_balance / 10**18:.4f} U
    After:  {final_client_balance / 10**18:.4f} U
    Change: {client_diff / 10**18:+.4f} U

  Provider (Agent B):
    Before: {initial_provider_balance / 10**18:.4f} U
    After:  {final_provider_balance / 10**18:.4f} U
    Change: {provider_diff / 10**18:+.4f} U
{'─'*60}
""")

    print(f"""
{'#'*60}
  Demo Complete!
{'#'*60}
  This demo showed:
  1. ERC-8004 agent discovery (SDK: ERC8004Agent.get_agent_info)
  2. Agent-to-agent service negotiation (SDK: POST /apex/negotiate)
  3. Trustless escrow via ERC-8183 (SDK: APEXClient)
  4. IPFS-backed deliverable verification (SDK: submit_result)
  5. APEX Evaluator dispute resolution (SDK: APEXEvaluatorClient)
  6. On-chain payment settlement (money flow visible above)
  All running on BNB Chain testnet.
{'#'*60}
""")


if __name__ == "__main__":
    asyncio.run(main())

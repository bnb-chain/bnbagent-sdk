"""
Client Workflow - Create and monitor ACP jobs.

This example shows how a client creates jobs, funds them,
and monitors until completion.

Usage:
    python client_workflow.py

Environment:
    RPC_URL                  - Blockchain RPC endpoint
    ACP_ADDRESS              - ACP contract address
    OOV3_EVALUATOR_ADDRESS   - OOv3Evaluator contract address
    PRIVATE_KEY              - Client wallet private key
    AGENT_ADDRESS            - Provider agent address
"""

import os
import time
import sys
from dotenv import load_dotenv

load_dotenv()


def main():
    # Check required env vars
    required = ["RPC_URL", "ACP_ADDRESS", "OOV3_EVALUATOR_ADDRESS", "PRIVATE_KEY", "AGENT_ADDRESS"]
    missing = [k for k in required if not os.getenv(k)]
    
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)
    
    rpc_url = os.getenv("RPC_URL")
    acp_address = os.getenv("ACP_ADDRESS")
    evaluator_address = os.getenv("OOV3_EVALUATOR_ADDRESS")
    private_key = os.getenv("PRIVATE_KEY")
    agent_address = os.getenv("AGENT_ADDRESS")
    
    print("=" * 60)
    print("ACP Client Workflow")
    print("=" * 60)
    print()
    
    # Initialize
    from web3 import Web3
    from bnbagent import ACPClient, ACPStatus, OOv3EvaluatorClient
    
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        poa_middleware = ExtraDataToPOAMiddleware
    except ImportError:
        from web3.middleware import geth_poa_middleware
        poa_middleware = geth_poa_middleware
    
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(poa_middleware, layer=0)
    
    acp = ACPClient(
        web3=w3,
        contract_address=acp_address,
        private_key=private_key,
    )
    
    evaluator = OOv3EvaluatorClient(
        web3=w3,
        contract_address=evaluator_address,
    )
    
    client_address = w3.eth.account.from_key(private_key).address
    print(f"Client:    {client_address}")
    print(f"Provider:  {agent_address}")
    print(f"Evaluator: {evaluator_address}")
    print()
    
    # =========================================================================
    # Step 1: Create Job
    # =========================================================================
    
    print("-" * 60)
    print("Step 1: Create Job")
    print("-" * 60)
    
    # Use default 73-hour expiry
    # This provides buffer for OOv3 liveness + potential DVM disputes
    from bnbagent.acp_client import get_default_expiry
    expiry = get_default_expiry()  # 73 hours
    description = "Analyze blockchain news for Q1 2026"
    
    print(f"Description: {description}")
    print(f"Expiry:      {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry))}")
    print()
    
    result = acp.create_job(
        provider=agent_address,
        evaluator=evaluator_address,
        expired_at=expiry,
        description=description,
        hook=evaluator_address,  # Same as evaluator for auto-assertion
    )
    
    job_id = result["jobId"]
    print(f"Created job #{job_id}")
    print(f"TX: {result['transactionHash']}")
    print()
    
    # =========================================================================
    # Step 2: Set Budget
    # =========================================================================
    
    print("-" * 60)
    print("Step 2: Set Budget")
    print("-" * 60)
    
    budget = 10 * 10**18  # 10 U
    print(f"Budget: {budget / 10**18} tokens")
    
    result = acp.set_budget(job_id, budget)
    print(f"TX: {result['transactionHash']}")
    print()
    
    # =========================================================================
    # Step 3: Fund Job
    # =========================================================================
    
    print("-" * 60)
    print("Step 3: Fund Job")
    print("-" * 60)
    
    # Note: You need to approve the ACP contract to spend your tokens first!
    # token.approve(acp_address, budget)
    
    print("NOTE: Make sure you have approved the ACP contract to spend your tokens")
    print(f"      Token: {acp.payment_token()}")
    print(f"      Amount: {budget}")
    print()
    
    proceed = input("Have you approved the token spend? (y/N): ").strip().lower()
    if proceed != "y":
        print("Please approve first, then run again with existing job_id")
        print(f"Job ID: {job_id}")
        sys.exit(0)
    
    result = acp.fund(job_id, budget)
    print(f"Funded! TX: {result['transactionHash']}")
    print()
    
    # =========================================================================
    # Step 4: Wait for Agent
    # =========================================================================
    
    print("-" * 60)
    print("Step 4: Wait for Agent to Submit")
    print("-" * 60)
    
    print("Job is now FUNDED. The agent should process it and submit.")
    print("Checking status every 30 seconds...")
    print()
    
    while True:
        job = acp.get_job(job_id)
        status = ACPStatus(job["status"])
        
        if status == ACPStatus.FUNDED:
            print(f"  Status: FUNDED (waiting for agent)")
            time.sleep(30)
            continue
        elif status == ACPStatus.SUBMITTED:
            print(f"  Status: SUBMITTED")
            break
        elif status == ACPStatus.COMPLETED:
            print(f"  Status: COMPLETED")
            print("Job completed successfully!")
            sys.exit(0)
        elif status == ACPStatus.REJECTED:
            print(f"  Status: REJECTED")
            print("Job was rejected. Check the dispute details.")
            sys.exit(1)
        else:
            print(f"  Status: {status.name}")
            break
    
    # =========================================================================
    # Step 5: Monitor Assertion
    # =========================================================================
    
    print("-" * 60)
    print("Step 5: Monitor Assertion")
    print("-" * 60)
    
    while True:
        info = evaluator.get_assertion_info(job_id)
        
        if not info.initiated:
            print("  Assertion not yet initiated")
            time.sleep(30)
            continue
        
        if info.disputed:
            print("  DISPUTED! Awaiting UMA DVM resolution")
            print(f"  Assertion ID: {info.assertion_id.hex()}")
            print()
            print("To dispute, the client would have called disputeAssertion()")
            print("The UMA DVM will now vote on the outcome.")
            break
        
        if info.settleable:
            print("  Liveness period ended!")
            print("  Settling...")
            
            # Anyone can settle
            result = evaluator.settle_job(job_id)
            print(f"  Settled! TX: {result['transactionHash']}")
            break
        
        remaining = info.liveness_end - int(time.time())
        print(f"  In liveness period: {remaining}s remaining")
        time.sleep(min(60, remaining + 5))
    
    # =========================================================================
    # Final Status
    # =========================================================================
    
    print("-" * 60)
    print("Final Status")
    print("-" * 60)
    
    job = acp.get_job(job_id)
    status = ACPStatus(job["status"])
    
    print(f"Job #{job_id}: {status.name}")
    
    if status == ACPStatus.COMPLETED:
        print("Agent was paid successfully!")
    elif status == ACPStatus.REJECTED:
        print("Job was rejected. You can claim a refund.")
        print("Call: acp.claim_refund(job_id)")


if __name__ == "__main__":
    main()

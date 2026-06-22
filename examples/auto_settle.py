"""Asynchronous auto-settle script for ERC-8183 jobs.

This script polls for ``JobSubmitted`` events, calculates if the dispute
window has passed, and then permissionlessly calls ``settle()``.
It demonstrates resilience by using asyncio to poll and handle RPC errors.
"""

import asyncio
import logging
import os
import time

from dotenv import load_dotenv

from bnbagent.erc8183 import ERC8183Client, JobStatus
from bnbagent.wallets import EVMWalletProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("auto_settle")


async def auto_settle_loop(client: ERC8183Client, poll_interval: int = 15):
    """Continuously poll for submitted jobs and settle them when ready."""
    logger.info(f"Starting auto-settler on network: {client.network.name}")

    while True:
        try:
            # Fetch the latest job counter and scan recent jobs
            job_counter = await asyncio.to_thread(client.commerce.job_counter)
            logger.debug(f"Current job counter: {job_counter}")

            # Check the last 50 jobs for simplicity in this example
            start_job = max(1, job_counter - 50)
            for job_id in range(start_job, job_counter + 1):
                try:
                    job = await asyncio.to_thread(client.commerce.get_job, job_id)
                except Exception as e:
                    # Job might not exist or RPC might hiccup on specific calls
                    logger.debug(f"Failed to fetch job {job_id}: {e}")
                    continue

                if job.status == JobStatus.SUBMITTED:
                    now = int(time.time())
                    
                    # We use expiredAt as the universal escape hatch, but rely on 
                    # router.settle's internal check for the dispute window.
                    if job.expired_at <= now:
                        logger.debug(f"Job {job_id} expired, waiting for claimRefund flow.")
                        continue

                    logger.info(f"Job {job_id} is SUBMITTED. Attempting to settle...")
                    try:
                        # client.settle() delegates to router.settle(), which pulls the verdict.
                        # If the dispute window hasn't passed, it will revert.
                        result = await asyncio.to_thread(client.settle, job_id)
                        tx_hash = result.get('transactionHash')
                        logger.info(f"Successfully settled Job {job_id}. Tx: {tx_hash}")
                    except Exception as e:
                        # Expected to fail if the dispute window is still open
                        logger.debug(f"Cannot settle Job {job_id} yet (likely dispute window open): {e}")
                else:
                    # Optional: log other statuses at debug level to avoid console spam
                    pass

        except Exception as e:
            logger.error(f"RPC Error or network drop: {e}. Retrying in {poll_interval}s...")

        await asyncio.sleep(poll_interval)


async def main():
    load_dotenv()

    # Following the standard SDK pattern: password is required, key is optional after 1st run
    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD")
    network = os.getenv("NETWORK", "bsc-testnet")

    if not wallet_password:
        logger.error("WALLET_PASSWORD must be set in .env")
        return

    # persist=True is default, allowing the keystore to be saved for future runs
    wallet = EVMWalletProvider(
        password=wallet_password, 
        private_key=private_key, 
        persist=False if not private_key else True
    )
    client = ERC8183Client(wallet, network=network)

    await auto_settle_loop(client)

if __name__ == "__main__":
    asyncio.run(main())

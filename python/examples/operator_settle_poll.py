"""Poll and settle ERC-8183 jobs from a separate trusted operator process.

This process holds a gas-paying wallet and scans submitted jobs after their
dispute window. It must not run inside an HTTP, A2A, MCP, or agent/LLM process:
keeping settlement operationally separate prevents a compromised agent from
turning an example loop into an ambient signing capability.
"""

import asyncio
import logging
import os
import time

from dotenv import load_dotenv

from bnbagent.erc8183 import ERC8183Client, JobStatus
from bnbagent.wallets import EVMWalletProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("operator_settle_poll")


async def operator_settle_loop(client: ERC8183Client, poll_interval: int = 15):
    """Continuously poll for submitted jobs and settle them when ready."""
    logger.info("Starting operator settler on network: %s", client.network.name)

    while True:
        try:
            job_counter = await asyncio.to_thread(client.commerce.job_counter)
            logger.debug("Current job counter: %s", job_counter)

            start_job = max(1, job_counter - 50)
            for job_id in range(start_job, job_counter + 1):
                try:
                    job = await asyncio.to_thread(client.commerce.get_job, job_id)
                except Exception as exc:
                    logger.debug("Failed to fetch job %s: %s", job_id, exc)
                    continue

                if job.status != JobStatus.SUBMITTED:
                    continue
                if job.expired_at <= int(time.time()):
                    logger.debug("Job %s expired, waiting for claimRefund flow", job_id)
                    continue

                logger.info("Job %s is SUBMITTED. Attempting to settle", job_id)
                try:
                    result = await asyncio.to_thread(client.settle, job_id)
                    logger.info(
                        "Successfully settled Job %s. Tx: %s",
                        job_id,
                        result.get("transactionHash"),
                    )
                except Exception as exc:
                    logger.debug(
                        "Cannot settle Job %s yet (likely dispute window open): %s",
                        job_id,
                        exc,
                    )
        except Exception as exc:
            logger.error(
                "RPC error or network drop: %s. Retrying in %ss",
                exc,
                poll_interval,
            )

        await asyncio.sleep(poll_interval)


async def main():
    load_dotenv()
    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD")
    network = os.getenv("NETWORK", "bsc-testnet")

    if not wallet_password:
        logger.error("WALLET_PASSWORD must be set in .env")
        return

    wallet = EVMWalletProvider(password=wallet_password, private_key=private_key)
    client = ERC8183Client(wallet, network=network)
    await operator_settle_loop(client)


if __name__ == "__main__":
    asyncio.run(main())

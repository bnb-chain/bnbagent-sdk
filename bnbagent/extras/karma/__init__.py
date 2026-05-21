"""
Karma Verifiable Evaluator — BNB Chain ERC-8183 integration.

Plugs Karma's signed-receipt + evidence-bundle verification into the
ERC-8183 settlement lifecycle as a pre-submit evaluator.

Key components
--------------
- ``KarmaEvaluator``      — verifies Karma evidence bundles before ``settle()``.
- ``KarmaBNBVerifier``    — high-level verifier that wraps ``KarmaEvaluator``
  and integrates with ``ERC8183Client``.
- ``KarmaEvidenceStore``  — lightweight in-memory cache for Karma receipts.
- ``KarmaReceiptSigner``  — EIP-191 compatible receipt signer for on-chain anchoring.

Quickstart
----------
    from bnbagent import ERC8183Client, EVMWalletProvider
    from bnbagent.extras.karma import KarmaEvaluator, KarmaBNBVerifier

    wallet = EVMWalletProvider(password="...", private_key="0x...")
    erc8183 = ERC8183Client(wallet, network="bsc-testnet")

    evaluator = KarmaEvaluator(
        runtime_url="https://api.karma.xyz",
        api_key="karma_secret",
    )

    verifier = KarmaBNBVerifier(erc8183, evaluator)

    # After the provider submits the deliverable, verify it with Karma:
    result = await verifier.verify_and_settle(job_id)
    # result contains Karma's VerificationResult + on-chain settlement tx

Install
-------
    pip install "bnbagent[karma]"
"""

from __future__ import annotations

from .evaluator import (
    KarmaBNBVerifier,
    KarmaEvaluator,
    KarmaEvidenceStore,
    KarmaReceiptSigner,
)

__all__ = [
    "KarmaEvaluator",
    "KarmaBNBVerifier",
    "KarmaEvidenceStore",
    "KarmaReceiptSigner",
]

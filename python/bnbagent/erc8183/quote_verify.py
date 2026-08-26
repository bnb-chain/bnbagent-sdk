"""Reference verification for ERC-8183 provider quote signatures."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import defunct_hash_message, encode_defunct
from web3 import Web3

from .negotiation import _build_description_content

_ERC1271_MAGIC_VALUE = "0x1626ba7e"
_ERC1271_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "hash", "type": "bytes32"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"},
        ],
        "name": "isValidSignature",
        "outputs": [{"internalType": "bytes4", "name": "magicValue", "type": "bytes4"}],
        "stateMutability": "view",
        "type": "function",
    }
]
_HASH_HEX = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SIGNATURE_HEX = re.compile(r"^0x(?:[0-9a-fA-F]{2})+$")


@dataclass(frozen=True)
class QuoteSignatureVerdict:
    """Cryptographic quote-verification result.

    RPC failures deliberately propagate instead of being reported as an
    invalid signature: unavailable historical state is not a cryptographic
    verdict and callers should treat it as retryable.
    """

    valid: bool
    method: str | None = None
    signer: str | None = None
    reason: str | None = None


def _invalid(reason: str) -> QuoteSignatureVerdict:
    return QuoteSignatureVerdict(valid=False, reason=reason)


def _signed_content(envelope: dict[str, Any]) -> dict[str, Any]:
    response = envelope.get("response")
    if isinstance(response, dict):
        return _build_description_content(
            envelope,
            chain_id=envelope.get("chain_id"),
            verifying_contract=envelope.get("verifying_contract"),
        )
    return {
        key: value
        for key, value in envelope.items()
        if key not in {"negotiation_hash", "provider_sig"}
    }


def _quote_expiry(envelope: dict[str, Any]) -> Any:
    response = envelope.get("response")
    if isinstance(response, dict):
        return envelope.get("quote_expires_at") or response.get("quote_expires_at")
    return envelope.get("quote_expires_at")


def verify_quote_signature(
    *,
    envelope: dict[str, Any],
    provider: str,
    w3: Web3,
    expected_verifying_contract: str | None = None,
    block_number: int | None = None,
) -> QuoteSignatureVerdict:
    """Verify an EIP-191 or ERC-1271 provider quote at a chain block.

    ``block_number`` should be the job's ``JobFunded`` block. This anchors
    expiry and contract-account authorization to the moment the buyer
    economically accepted the quote, rather than today's mutable state.
    """

    negotiation_hash = envelope.get("negotiation_hash")
    provider_sig = envelope.get("provider_sig")
    if not isinstance(negotiation_hash, str) or not _HASH_HEX.fullmatch(negotiation_hash):
        return _invalid("missing or invalid negotiation_hash")
    if not isinstance(provider_sig, str) or not _SIGNATURE_HEX.fullmatch(provider_sig):
        return _invalid("missing or invalid provider_sig")

    signed_verifier = envelope.get("verifying_contract")
    checker: str | None = None
    if signed_verifier is not None:
        if not isinstance(signed_verifier, str):
            return _invalid("invalid verifying_contract")
        try:
            checker = Web3.to_checksum_address(signed_verifier)
        except (TypeError, ValueError):
            return _invalid("invalid verifying_contract")
    if expected_verifying_contract is not None:
        if checker is None:
            return _invalid("quote is not bound to a verifying_contract")
        try:
            expected_checker = Web3.to_checksum_address(expected_verifying_contract)
        except (TypeError, ValueError):
            return _invalid("invalid expected verifying_contract")
        if checker != expected_checker:
            return _invalid("verifying_contract mismatch")

    signed_chain_id = envelope.get("chain_id")
    if signed_chain_id is not None:
        if (
            not isinstance(signed_chain_id, int)
            or isinstance(signed_chain_id, bool)
            or signed_chain_id <= 0
        ):
            return _invalid("invalid chain_id")
        if w3.eth.chain_id != signed_chain_id:
            return _invalid("chain_id mismatch")

    try:
        canonical = json.dumps(
            _signed_content(envelope), sort_keys=True, separators=(",", ":")
        )
        recomputed = Web3.keccak(text=canonical).hex()
        if not recomputed.startswith("0x"):
            recomputed = "0x" + recomputed
    except (TypeError, ValueError):
        return _invalid("invalid quote content")
    if recomputed.lower() != negotiation_hash.lower():
        return _invalid("negotiation_hash mismatch")

    expiry = _quote_expiry(envelope)
    if expiry is not None:
        if not isinstance(expiry, int) or isinstance(expiry, bool):
            return _invalid("invalid quote_expires_at")
        block = w3.eth.get_block(block_number if block_number is not None else "latest")
        if expiry <= int(block["timestamp"]):
            return _invalid("quote has expired")

    try:
        provider_address = Web3.to_checksum_address(provider)
    except (TypeError, ValueError):
        return _invalid("invalid provider address")

    try:
        recovered = Account.recover_message(
            encode_defunct(text=negotiation_hash), signature=provider_sig
        )
        if Web3.to_checksum_address(recovered) == provider_address:
            return QuoteSignatureVerdict(
                valid=True, method="eip191", signer=provider_address
            )
    except (TypeError, ValueError):
        # Contract-account signatures are often not 65-byte EOA signatures.
        pass

    block_identifier: int | str = block_number if block_number is not None else "latest"
    bytecode = w3.eth.get_code(provider_address, block_identifier=block_identifier)
    if not bytecode:
        return _invalid("provider signature is not valid")

    contract = w3.eth.contract(address=provider_address, abi=_ERC1271_ABI)
    call_transaction = {"from": checker} if checker else {}
    digest = defunct_hash_message(text=negotiation_hash)
    signature_bytes = bytes.fromhex(provider_sig[2:])
    result = contract.functions.isValidSignature(digest, signature_bytes).call(
        call_transaction,
        block_identifier=block_identifier,
    )
    magic = Web3.to_hex(result).lower()
    if magic != _ERC1271_MAGIC_VALUE:
        return _invalid("ERC-1271 account rejected provider_sig")

    return QuoteSignatureVerdict(valid=True, method="erc1271", signer=provider_address)

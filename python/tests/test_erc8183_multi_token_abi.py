"""ABI contract tests for the multi-token Commerce client surface."""

from __future__ import annotations

import json
from pathlib import Path

from web3 import Web3

import bnbagent
import bnbagent.erc8183 as erc8183
from bnbagent.core.abis import load_abi

REPO_ROOT = Path(__file__).resolve().parents[2]
ABI_PATH = REPO_ROOT / "abis" / "AgenticCommerce.json"


def _commerce_abi() -> list[dict]:
    return json.loads(ABI_PATH.read_text(encoding="utf-8"))


def _entry(entry_type: str, name: str) -> dict:
    return next(
        item
        for item in _commerce_abi()
        if item.get("type") == entry_type and item.get("name") == name
    )


def test_multi_token_abi_surface_and_selectors_are_published():
    create = _entry("function", "createJobWithToken")
    assert [item["type"] for item in create["inputs"]] == [
        "address",
        "address",
        "uint256",
        "string",
        "address",
        "address",
    ]
    assert (
        Web3.keccak(text="createJobWithToken(address,address,uint256,string,address,address)")[
            :4
        ].hex()
        == "e1623ca4"
    )

    assert _entry("function", "jobPaymentToken")["outputs"][0]["type"] == "address"
    assert _entry("function", "isPaymentTokenSupported")["outputs"][0]["type"] == "bool"
    assert [item["type"] for item in _entry("event", "JobPaymentTokenBound")["inputs"]] == [
        "uint256",
        "address",
    ]


def test_legacy_create_selector_and_job_tuple_remain_compatible():
    create = _entry("function", "createJob")
    assert [item["type"] for item in create["inputs"]] == [
        "address",
        "address",
        "uint256",
        "string",
        "address",
    ]
    assert Web3.keccak(text="createJob(address,address,uint256,string,address)")[:4].hex() == (
        "41528812"
    )

    expected_job_fields = [
        ("id", "uint256"),
        ("client", "address"),
        ("provider", "address"),
        ("evaluator", "address"),
        ("description", "string"),
        ("budget", "uint256"),
        ("expiredAt", "uint256"),
        ("status", "uint8"),
        ("hook", "address"),
        ("submittedAt", "uint256"),
        ("deliverable", "bytes32"),
    ]
    jobs = _entry("function", "jobs")
    assert [(item["name"], item["type"]) for item in jobs["outputs"]] == expected_job_fields
    get_job = _entry("function", "getJob")
    assert [
        (item["name"], item["type"]) for item in get_job["outputs"][0]["components"]
    ] == expected_job_fields


def test_runtime_abi_load_and_public_exports_include_multi_token_types():
    loaded = load_abi("AgenticCommerce.json")
    names = {item.get("name") for item in loaded}
    assert {"createJobWithToken", "jobPaymentToken", "isPaymentTokenSupported"} <= names
    assert erc8183.TokenMetadata is bnbagent.TokenMetadata
    assert erc8183.JobPaymentTokenMismatchError is bnbagent.JobPaymentTokenMismatchError

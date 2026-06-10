"""Manage a NodeReal MegaFuel paymaster policy whitelist.

Sponsor-side maintenance script. Set the secrets in the project-root ``.env``
(see ``.env.example`` for the keys: ``PAYMASTER_API_KEY`` plus the per-network
``PAYMASTER_TESTNET_POLICY_UUID`` / ``PAYMASTER_MAINNET_POLICY_UUID``), then
run:

    uv run python scripts/setup_paymaster.py <network> <action>

    # examples
    python scripts/setup_paymaster.py testnet list
    python scripts/setup_paymaster.py testnet add
    python scripts/setup_paymaster.py mainnet remove
    python scripts/setup_paymaster.py testnet clear

Both args are optional; defaults are DEFAULT_NETWORK / DEFAULT_ACTION.

Supported actions:
- "list"   : show policy info + every whitelist entry
- "add"    : add the build_targets() values to their whitelists
- "remove" : remove the build_targets() values from their whitelists
- "clear"  : remove EVERY entry currently on the CLEAR_TYPES whitelists

Default targets pin the ERC-8004 IdentityRegistry plus the ERC-8183
AgenticCommerce / EvaluatorRouter / OptimisticPolicy contracts to
ToAccountWhitelist, and every nonpayable method an agent / voter / sponsor
sends to those contracts to ContractMethodSigWhitelist (see CONTRACT_METHODS
for the full selector -> signature map).

Docs:
- https://docs.nodereal.io/reference/pm-addtowhitelist
- https://docs.nodereal.io/reference/pm-rmfromwhitelist
- https://docs.nodereal.io/reference/pm-getwhitelist
- https://docs.nodereal.io/reference/pm-getpolicybyuuid
- https://docs.nodereal.io/docs/megafuel-policy-management
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from web3 import Web3

# Load secrets from the project-root .env (this script lives in scripts/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# =====================================================================
# CONFIG -- secrets come from .env (see .env.example).
# =====================================================================

# NodeReal API key (from https://meganode.nodereal.io/).
API_KEY: str = os.getenv("PAYMASTER_API_KEY", "")

# Per-network config. Each network has its own MegaFuel endpoint,
# one paymaster policy UUID (read from .env), the ERC-8004 IdentityRegistry
# address, and the ERC-8183 AgenticCommerce address. Contract addresses
# mirror bnbagent/config.py NETWORKS.
NETWORKS: dict[str, dict[str, str]] = {
    "testnet": {
        "endpoint": "https://open-platform-ap.nodereal.io/{api_key}/megafuel-testnet",
        "policy_uuid": os.getenv("PAYMASTER_TESTNET_POLICY_UUID", ""),
        "erc8004_registry": "0x8004A818BFB912233c491871b3d84c89A494BD9e",
        "erc8183_commerce": "0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de",
        "erc8183_router": "0xd7d36d66d2f1b608a0f943f722d27e3744f66f25",
        "erc8183_policy": "0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6",
    },
    "mainnet": {
        "endpoint": "https://open-platform-ap.nodereal.io/{api_key}/megafuel",
        "policy_uuid": os.getenv("PAYMASTER_MAINNET_POLICY_UUID", ""),
        "erc8004_registry": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",  # TBD when ERC-8004 ships on BSC mainnet
        "erc8183_commerce": "0xea4daa3100a767e86fded867729ae7446476eba6",
        "erc8183_router": "0x51895229e12f9876011789b04f8698af06ccd6da",
        "erc8183_policy": "0x9c01845705b3078aa2e8cff7520a6376fd766de5",
    },
}

# Defaults when no CLI args are passed.
DEFAULT_NETWORK = "testnet"
DEFAULT_ACTION = "list"

# When ACTION == "clear", only these whitelist types are wiped.
# (Safety guard: avoids accidentally nuking the From-account whitelist.)
CLEAR_TYPES: list[str] = [
    "ToAccountWhitelist",
    "ContractMethodSigWhitelist",
]

# ---- whitelist targets for ACTION == "add" / "remove" ------------------
# Every nonpayable method an agent / voter / sponsor sends as a transaction,
# keyed by 4-byte selector -> signature. Selectors computed from the ABIs via
# `bytes4(keccak256(bytes(signature)))`. The signature doubles as a label in
# the "list" output so whitelist entries are human-checkable.
CONTRACT_METHODS: dict[str, str] = {
    # ERC-8004 IdentityRegistry — agent self-management.
    # ABI: bnbagent/erc8004/abis/IdentityRegistry.json
    "0x1aa3a008": "register()",
    "0xf2c298be": "register(string)",
    "0x8ea42286": "register(string,(string,bytes)[])",
    "0x466648da": "setMetadata(uint256,string,bytes)",
    "0x0af28bd3": "setAgentURI(uint256,string)",
    "0x2d1ef5ae": "setAgentWallet(uint256,address,uint256,bytes)",
    # ERC-8183 AgenticCommerce — full job lifecycle.
    # ABI: bnbagent/erc8183/abis/AgenticCommerce.json
    "0x41528812": "createJob(address,address,uint256,string,address)",
    "0xc9a84bb9": "setProvider(uint256,address,bytes)",
    "0xdd4ae9d4": "setBudget(uint256,uint256,bytes)",
    "0xd2e13f50": "fund(uint256,uint256,bytes)",
    "0x9e63798d": "submit(uint256,bytes32,bytes)",
    "0xd75bbdf3": "complete(uint256,bytes32,bytes)",
    "0x41dd26f5": "reject(uint256,bytes32,bytes)",
    "0x5b7baf64": "claimRefund(uint256)",
    # ERC-8183 OptimisticPolicy — dispute / voting.
    # (Owner-only voter admin — addVoter/removeVoter/setQuorum — is DAO ops,
    # not a gas-sponsored agent action, so it is intentionally omitted.)
    # ABI: bnbagent/erc8183/abis/OptimisticPolicy.json
    "0x86d6282c": "dispute(uint256)",
    "0xfedf9462": "voteReject(uint256)",
    # ERC-8183 EvaluatorRouter — job routing / settlement.
    # ABI: bnbagent/erc8183/abis/EvaluatorRouter.json
    "0x51d5456d": "registerJob(uint256,address)",
    "0x39c2ebb9": "settle(uint256,bytes)",
    "0x77fd00c5": "markExpired(uint256)",
}

CONTRACT_METHOD_SELECTORS: list[str] = list(CONTRACT_METHODS)


def build_targets(net_cfg: dict[str, str]) -> dict[str, list[str]]:
    """Return {whitelistType: [values]} for the active network.

    Override or extend per project (e.g. add FromAccountWhitelist senders).
    """
    to_accounts = [
        net_cfg[key]
        for key in (
            "erc8004_registry",
            "erc8183_commerce",
            "erc8183_router",
            "erc8183_policy",
        )
        if net_cfg.get(key)
    ]
    return {
        "ToAccountWhitelist": to_accounts,
        "ContractMethodSigWhitelist": CONTRACT_METHOD_SELECTORS,
        # "FromAccountWhitelist": ["0xYourAgentAddressHere"],
        # "BEP20ReceiverWhiteList": [],
    }


# =====================================================================
# Implementation -- you generally don't need to edit below this line.
# =====================================================================

WHITELIST_TYPES = (
    "FromAccountWhitelist",
    "ToAccountWhitelist",
    "BEP20ReceiverWhiteList",
    "ContractMethodSigWhitelist",
)

# pm_getWhitelist enforces both offset and limit < 100000.
PAGE_LIMIT = 1000


def _normalize(whitelist_type: str, raw: str) -> str:
    """Validate and canonicalize a value for the given whitelist type."""
    if whitelist_type == "ContractMethodSigWhitelist":
        if not (raw.startswith("0x") and len(raw) == 10):
            raise ValueError(
                f"Invalid 4-byte selector {raw!r} (expected 0x + 8 hex chars)"
            )
        int(raw, 16)  # raises if not hex
        return raw.lower()

    if not Web3.is_address(raw):
        raise ValueError(f"Invalid 20-byte address {raw!r}")
    return Web3.to_checksum_address(raw)


def _resolve_network(network: str) -> dict[str, str]:
    if not API_KEY or API_KEY.startswith("<"):
        raise SystemExit("API_KEY is not set -- edit the CONFIG block.")
    if network not in NETWORKS:
        raise SystemExit(f"network {network!r} not in NETWORKS={list(NETWORKS)}")
    cfg = NETWORKS[network]
    if not cfg.get("endpoint"):
        raise SystemExit(f"NETWORKS[{network!r}] missing 'endpoint'")
    if not cfg.get("policy_uuid"):
        raise SystemExit(f"NETWORKS[{network!r}] missing 'policy_uuid'")
    return cfg


def _endpoint(net_cfg: dict[str, str]) -> str:
    return net_cfg["endpoint"].format(api_key=API_KEY)


def _rpc(url: str, method: str, params: list, request_id: int = 1):
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        err = body["error"]
        raise RuntimeError(
            f"RPC error [{err.get('code', '?')}]: {err.get('message', err)}"
        )
    return body.get("result")


def fetch_whitelist(url: str, policy_uuid: str, whitelist_type: str) -> list[str]:
    """Page through pm_getWhitelist until exhausted.

    NOTE: NodeReal's docs list `offset`/`limit` as strings, but the server
    rejects strings with `cannot unmarshal string into ... int`. They must
    be sent as JSON numbers.
    """
    out: list[str] = []
    offset = 0
    while True:
        page = (
            _rpc(
                url,
                "pm_getWhitelist",
                [
                    {
                        "policyUuid": policy_uuid,
                        "whitelistType": whitelist_type,
                        "offset": offset,
                        "limit": PAGE_LIMIT,
                    }
                ],
            )
            or []
        )
        out.extend(page)
        if len(page) < PAGE_LIMIT:
            break
        offset += len(page)
    return out


def _show(entry) -> str:
    """Render a raw whitelist entry for display (some come back as dicts)."""
    if isinstance(entry, (dict, list)):
        return json.dumps(entry, separators=(",", ":"))
    return str(entry)


def _selector_of(entry) -> str | None:
    """Pull a 4-byte selector (0x + 8 hex) out of a whitelist entry.

    pm_getWhitelist returns ContractMethodSigWhitelist entries as objects
    (e.g. {contractAddress, methodSig}), not plain selector strings, so we
    scan for the first selector-shaped value.
    """

    def _is_selector(s) -> bool:
        if not isinstance(s, str) or not (s.startswith("0x") and len(s) == 10):
            return False
        try:
            int(s, 16)
            return True
        except ValueError:
            return False

    if _is_selector(entry):
        return entry
    if isinstance(entry, dict):
        for val in entry.values():
            if _is_selector(val):
                return val
    return None


def cmd_list(url: str, uuid: str) -> int:
    try:
        info = _rpc(url, "pm_getPolicyByUuid", [uuid])
    except (requests.RequestException, RuntimeError) as e:
        print(f" pm_getPolicyByUuid -> ERROR: {e}")
        info = None

    if info:
        print("--- policy ---")
        for k in (
            "name",
            "type",
            "network",
            "activated",
            "remainingBalance",
            "sponsoredGasfee",
            "maxGasCost",
            "maxGasCostPerAddr",
            "maxGasCostPerAddrPerDay",
            "maxTxCountPerAddrPerDay",
            "fromWhitelistEnabled",
            "toWhitelistEnabled",
            "contractMethodSigWhitelistEnabled",
            "bep20ReceiverWhitelistEnabled",
        ):
            if k in info:
                print(f"  {k:34s} {info[k]}")
        print()

    print("--- whitelists ---")
    for wl in WHITELIST_TYPES:
        try:
            values = fetch_whitelist(url, uuid, wl)
        except (requests.RequestException, RuntimeError) as e:
            print(f"  {wl}: ERROR {e}")
            continue
        print(f"  {wl} ({len(values)})")
        for v in values:
            if wl == "ContractMethodSigWhitelist":
                sel = _selector_of(v)
                shown = sel if sel else _show(v)
                name = CONTRACT_METHODS.get(sel.lower()) if sel else None
                print(f"    - {shown}  -> {name or '(unknown selector)'}")
            else:
                print(f"    - {_show(v)}")
    return 0


def _apply_change(
    url: str,
    uuid: str,
    targets: dict[str, list[str]],
    method: str,
    label: str,
) -> int:
    failures = 0
    if not targets:
        print("targets dict is empty -- nothing to do.")
        return 0

    for wl, values in targets.items():
        if wl not in WHITELIST_TYPES:
            print(f"  SKIP unknown whitelist type {wl!r}")
            failures += 1
            continue
        if not values:
            continue

        try:
            normalized = [_normalize(wl, v) for v in values]
        except ValueError as e:
            print(f"  FAIL {wl} validation: {e}")
            failures += 1
            continue

        print(f"{label} {wl} ({len(normalized)})")
        for v in normalized:
            print(f"  - {v}")

        try:
            result = _rpc(
                url,
                method,
                [
                    {
                        "policyUuid": uuid,
                        "whitelistType": wl,
                        "values": normalized,
                    }
                ],
            )
        except (requests.RequestException, RuntimeError) as e:
            print(f"  -> ERROR: {e}")
            failures += 1
            continue
        print(f"  -> result: {json.dumps(result)}")
    return failures


def cmd_add(url: str, uuid: str, targets: dict[str, list[str]]) -> int:
    return _apply_change(url, uuid, targets, "pm_addToWhitelist", "ADD")


def cmd_remove(url: str, uuid: str, targets: dict[str, list[str]]) -> int:
    return _apply_change(url, uuid, targets, "pm_rmFromWhitelist", "REMOVE")


def cmd_clear(url: str, uuid: str) -> int:
    failures = 0
    for wl in CLEAR_TYPES:
        if wl not in WHITELIST_TYPES:
            print(f"SKIP unknown whitelist type {wl!r}")
            failures += 1
            continue
        try:
            current = fetch_whitelist(url, uuid, wl)
        except (requests.RequestException, RuntimeError) as e:
            print(f"FAIL fetch {wl}: {e}")
            failures += 1
            continue

        if not current:
            print(f"CLEAR {wl}: already empty")
            continue

        print(f"CLEAR {wl} ({len(current)} entries)")
        for v in current:
            print(f"  - {v}")

        try:
            result = _rpc(
                url,
                "pm_rmFromWhitelist",
                [
                    {
                        "policyUuid": uuid,
                        "whitelistType": wl,
                        "values": current,
                    }
                ],
            )
        except (requests.RequestException, RuntimeError) as e:
            print(f"  -> ERROR: {e}")
            failures += 1
            continue
        print(f"  -> result: {json.dumps(result)}")
    return failures


def main(network: str, action: str) -> int:
    net_cfg = _resolve_network(network)
    url = _endpoint(net_cfg)
    uuid = net_cfg["policy_uuid"]
    targets = build_targets(net_cfg)

    print(f"Network  : {network}")
    print(f"Endpoint : {net_cfg['endpoint'].replace('{api_key}', '***')}")
    print(f"Policy   : {uuid}")
    print(f"Action   : {action}")
    print()

    if action == "list":
        failures = cmd_list(url, uuid)
    elif action == "add":
        failures = cmd_add(url, uuid, targets)
    elif action == "remove":
        failures = cmd_remove(url, uuid, targets)
    elif action == "clear":
        failures = cmd_clear(url, uuid)
    else:
        raise SystemExit(
            f"Unknown action {action!r}; pick one of: list, add, remove, clear"
        )

    print()
    if failures:
        print(f"Done with {failures} failure(s).")
        return 1
    print("Done.")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage a NodeReal MegaFuel paymaster policy whitelist.",
    )
    parser.add_argument(
        "network",
        nargs="?",
        default=DEFAULT_NETWORK,
        choices=sorted(NETWORKS.keys()),
        help=f"target network (default: {DEFAULT_NETWORK})",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default=DEFAULT_ACTION,
        choices=["list", "add", "remove", "clear"],
        help=f"action to perform (default: {DEFAULT_ACTION})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(main(args.network, args.action))

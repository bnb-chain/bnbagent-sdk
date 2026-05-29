"""On-chain address registry for BNB Chain deployments.

Source of truth for U-token (United Stables) payment-token deployments plus
the surrounding Pieverse commerce/router/policy proxies on bscTestnet (97) and
bsc-mainnet (56). Addresses originate from the operator's deployment manifest;
EIP-712 domain (``name="United Stables"`` / ``version="1"``) was verified
on-chain against the live ``DOMAIN_SEPARATOR()`` of both deployments.

Only ``payment_token`` is an EIP-712 verifyingContract (EIP-3009
``TransferWithAuthorization``). The remaining addresses are direct-call
targets (used via ``sign_transaction``) and are exposed here purely as a
lookup convenience — they are **not** added to any signing allowlist by
default.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from web3 import Web3

# ── Chain ids ──────────────────────────────────────────────────────────────

BSC_MAINNET_CHAIN_ID = 56
BSC_TESTNET_CHAIN_ID = 97

# ── EIP-712 domain metadata for the payment token (verified on-chain) ─────

PAYMENT_TOKEN_EIP712_NAME = "United Stables"
PAYMENT_TOKEN_EIP712_VERSION = "1"


@dataclass(frozen=True)
class DeployedAddresses:
    """Snapshot of one network's contract deployment."""

    payment_token: str
    treasury: str
    commerce_proxy: str
    commerce_impl: str
    router_proxy: str
    router_impl: str
    policy: str


# Raw addresses as provided by the operator deployment manifest. Stored
# pre-checksum to make the source easy to diff against the original manifest;
# the public ``BNB_CHAIN_ADDRESSES`` table below is the checksummed form.
_RAW: dict[int, dict[str, str]] = {
    BSC_TESTNET_CHAIN_ID: {
        "payment_token": "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
        "treasury": "0x1001b2C085345f388778A975648aA50bcfd0D134",
        "commerce_proxy": "0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de",
        "commerce_impl": "0xc0b74dc6b1c95b1452f678741e7907290587d69b",
        "router_proxy": "0xd7d36d66d2f1b608a0f943f722d27e3744f66f25",
        "router_impl": "0x9f42b71ae5990e6f5bb58a935fffe32b29a5374a",
        "policy": "0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6",
    },
    BSC_MAINNET_CHAIN_ID: {
        "payment_token": "0xcE24439F2D9C6a2289F741120FE202248B666666",
        "treasury": "0x000000000000000000000000000000000000dEaD",
        "commerce_proxy": "0xea4daa3100a767e86fded867729ae7446476eba6",
        "commerce_impl": "0x2788d06576ef83fdbeb00fb848e9fd896fc259e6",
        "router_proxy": "0x51895229e12f9876011789b04f8698af06ccd6da",
        "router_impl": "0xf0cf8f47e5c035f16247ff16e9f367e477ee5007",
        "policy": "0x9c01845705b3078aa2e8cff7520a6376fd766de5",
    },
}


def _build() -> dict[int, DeployedAddresses]:
    out: dict[int, DeployedAddresses] = {}
    field_names = {f.name for f in fields(DeployedAddresses)}
    for chain_id, raw in _RAW.items():
        missing = field_names - raw.keys()
        if missing:
            raise RuntimeError(f"chain {chain_id} missing addresses: {missing}")
        out[chain_id] = DeployedAddresses(
            **{k: Web3.to_checksum_address(v) for k, v in raw.items()}
        )
    return out


BNB_CHAIN_ADDRESSES: dict[int, DeployedAddresses] = _build()


def get_address(chain_id: int) -> DeployedAddresses:
    """Return the deployment snapshot for ``chain_id``.

    Raises:
        KeyError: if ``chain_id`` is not a known BNB Chain deployment.
    """
    try:
        return BNB_CHAIN_ADDRESSES[chain_id]
    except KeyError as e:
        raise KeyError(
            f"no BNB Chain deployment registered for chain_id={chain_id}; "
            f"known: {sorted(BNB_CHAIN_ADDRESSES)}"
        ) from e


def known_payment_tokens() -> frozenset[tuple[int, str]]:
    """``(chain_id, checksum_address)`` pairs of every registered payment token.

    Used as the default ``domain_allowlist`` seed for ``SigningPolicy``: a
    typed-data signature against any verifyingContract not in this set will be
    refused unless the caller explicitly extends the policy.
    """
    return frozenset(
        (cid, deploy.payment_token) for cid, deploy in BNB_CHAIN_ADDRESSES.items()
    )

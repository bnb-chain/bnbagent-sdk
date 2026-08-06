/**
 * On-chain address registry for BNB Chain deployments.
 *
 * Source of truth for U-token (United Stables) payment-token deployments
 * plus the surrounding Pieverse commerce/router/policy proxies on
 * bsc-testnet (97) and bsc-mainnet (56). Addresses originate from the
 * operator's deployment manifest; EIP-712 domain (`name="United Stables"` /
 * `version="1"`) was verified on-chain against the live `DOMAIN_SEPARATOR()`
 * of both deployments.
 *
 * Only `paymentToken` is an EIP-712 verifyingContract (EIP-3009
 * `TransferWithAuthorization`). The remaining addresses are direct-call
 * targets and are exposed here purely as a lookup convenience — they are
 * **not** added to any signing allowlist by default.
 */

import { getAddress as toChecksumAddress } from "viem";

// ── Chain ids ────────────────────────────────────────────────────────────

export const BSC_MAINNET_CHAIN_ID = 56;
export const BSC_TESTNET_CHAIN_ID = 97;

// ── EIP-712 domain metadata for the payment token (verified on-chain) ────

export const PAYMENT_TOKEN_EIP712_NAME = "United Stables";
export const PAYMENT_TOKEN_EIP712_VERSION = "1";

/** Snapshot of one network's contract deployment. */
export interface DeployedAddresses {
  readonly paymentToken: `0x${string}`;
  readonly treasury: `0x${string}`;
  readonly commerceProxy: `0x${string}`;
  readonly commerceImpl: `0x${string}`;
  readonly routerProxy: `0x${string}`;
  readonly routerImpl: `0x${string}`;
  readonly policy: `0x${string}`;
}

type RawAddresses = Record<keyof DeployedAddresses, string>;

// Raw addresses as provided by the operator deployment manifest. Stored
// pre-checksum to make the source easy to diff against the original
// manifest; the public `BNB_CHAIN_ADDRESSES` table below is the checksummed
// form.
const RAW: Record<number, RawAddresses> = {
  [BSC_TESTNET_CHAIN_ID]: {
    paymentToken: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
    treasury: "0x1001b2C085345f388778A975648aA50bcfd0D134",
    commerceProxy: "0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de",
    commerceImpl: "0xc0b74dc6b1c95b1452f678741e7907290587d69b",
    routerProxy: "0xd7d36d66d2f1b608a0f943f722d27e3744f66f25",
    routerImpl: "0x9f42b71ae5990e6f5bb58a935fffe32b29a5374a",
    policy: "0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6",
  },
  [BSC_MAINNET_CHAIN_ID]: {
    paymentToken: "0xcE24439F2D9C6a2289F741120FE202248B666666",
    treasury: "0x000000000000000000000000000000000000dEaD",
    commerceProxy: "0xea4daa3100a767e86fded867729ae7446476eba6",
    commerceImpl: "0x2788d06576ef83fdbeb00fb848e9fd896fc259e6",
    routerProxy: "0x51895229e12f9876011789b04f8698af06ccd6da",
    routerImpl: "0xf0cf8f47e5c035f16247ff16e9f367e477ee5007",
    policy: "0x9c01845705b3078aa2e8cff7520a6376fd766de5",
  },
};

function build(): Record<number, DeployedAddresses> {
  const out: Record<number, DeployedAddresses> = {};
  for (const [chainIdStr, raw] of Object.entries(RAW)) {
    const chainId = Number(chainIdStr);
    const checksummed = Object.fromEntries(
      Object.entries(raw).map(([field, addr]) => [
        field,
        toChecksumAddress(addr),
      ]),
    ) as unknown as DeployedAddresses;
    out[chainId] = Object.freeze(checksummed);
  }
  return Object.freeze(out);
}

export const BNB_CHAIN_ADDRESSES: Record<number, DeployedAddresses> = build();

/**
 * Return the deployment snapshot for `chainId`.
 *
 * @throws {Error} if `chainId` is not a known BNB Chain deployment.
 */
export function getAddress(chainId: number): DeployedAddresses {
  const deployed = BNB_CHAIN_ADDRESSES[chainId];
  if (deployed === undefined) {
    const known = Object.keys(BNB_CHAIN_ADDRESSES)
      .map(Number)
      .sort((a, b) => a - b);
    throw new Error(
      `no BNB Chain deployment registered for chain_id=${chainId}; known: [${known.join(", ")}]`,
    );
  }
  return deployed;
}

/**
 * `"chainId:checksumAddress"` keys of every registered payment token.
 *
 * Used as the default `domainAllowlist` seed for `SigningPolicy`: a
 * typed-data signature against any verifyingContract not in this set will be
 * refused unless the caller explicitly extends the policy.
 *
 * A fresh `Set` is built on every call, so mutating the returned instance
 * never leaks back into the registry.
 */
export function knownPaymentTokens(): ReadonlySet<string> {
  const out = new Set<string>();
  for (const [chainId, deploy] of Object.entries(BNB_CHAIN_ADDRESSES)) {
    out.add(`${chainId}:${deploy.paymentToken}`);
  }
  return out;
}

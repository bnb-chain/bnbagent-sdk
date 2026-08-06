import { getAddress as toChecksumAddress } from "viem";
import { describe, expect, it } from "vitest";
import {
  BNB_CHAIN_ADDRESSES,
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
  type DeployedAddresses,
  PAYMENT_TOKEN_EIP712_NAME,
  PAYMENT_TOKEN_EIP712_VERSION,
  getAddress,
  knownPaymentTokens,
} from "../src/networks/index.js";

/** Ports python/tests/test_networks_addresses.py. */

describe("getAddress", () => {
  it("returns the mainnet payment token", () => {
    const d = getAddress(BSC_MAINNET_CHAIN_ID);
    expect(d.paymentToken).toBe("0xcE24439F2D9C6a2289F741120FE202248B666666");
  });

  it("returns the testnet payment token", () => {
    const d = getAddress(BSC_TESTNET_CHAIN_ID);
    expect(d.paymentToken).toBe("0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565");
  });

  it("throws with chain id and known-ids list for an unknown chain", () => {
    expect(() => getAddress(1)).toThrow(
      "no BNB Chain deployment registered for chain_id=1; known: [56, 97]",
    );
  });
});

describe("BNB_CHAIN_ADDRESSES", () => {
  it("checksums every address field on every chain", () => {
    const fields: (keyof DeployedAddresses)[] = [
      "paymentToken",
      "treasury",
      "commerceProxy",
      "commerceImpl",
      "routerProxy",
      "routerImpl",
      "policy",
    ];
    for (const [chainId, deploy] of Object.entries(BNB_CHAIN_ADDRESSES)) {
      for (const field of fields) {
        const addr = deploy[field];
        expect(
          addr,
          `chain ${chainId} field ${field}=${addr} is not checksummed`,
        ).toBe(toChecksumAddress(addr));
      }
    }
  });

  it("is frozen at the top level", () => {
    expect(Object.isFrozen(BNB_CHAIN_ADDRESSES)).toBe(true);
  });

  it("rejects mutation of a per-chain DeployedAddresses instance", () => {
    const d = getAddress(BSC_MAINNET_CHAIN_ID);
    expect(Object.isFrozen(d)).toBe(true);
    expect(() => {
      // @ts-expect-error intentional mutation attempt for the immutability test
      d.paymentToken = "0xdeadbeef";
    }).toThrow();
  });
});

describe("knownPaymentTokens", () => {
  it("contains both networks' payment tokens keyed by chainId:checksumAddress", () => {
    const pairs = knownPaymentTokens();
    expect(
      pairs.has(
        `${BSC_MAINNET_CHAIN_ID}:0xcE24439F2D9C6a2289F741120FE202248B666666`,
      ),
    ).toBe(true);
    expect(
      pairs.has(
        `${BSC_TESTNET_CHAIN_ID}:0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`,
      ),
    ).toBe(true);
    expect(pairs.size).toBe(2); // update when adding more networks
  });

  it("is effectively immutable: mutating a returned set does not affect later calls", () => {
    const first = knownPaymentTokens() as Set<string>;
    first.add("intruder");
    const second = knownPaymentTokens();
    expect(second.has("intruder")).toBe(false);
    expect(second.size).toBe(2);
  });
});

describe("EIP-712 domain constants", () => {
  it("match the phase-0 on-chain verification", () => {
    // name+version constants must match what's encoded in U token's
    // DOMAIN_SEPARATOR on-chain (recovered by brute-forcing
    // keccak(name)|keccak(version)|chainId|verifyingContract against the
    // live DOMAIN_SEPARATOR() return value).
    expect(PAYMENT_TOKEN_EIP712_NAME).toBe("United Stables");
    expect(PAYMENT_TOKEN_EIP712_VERSION).toBe("1");
  });
});

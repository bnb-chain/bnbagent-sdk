import { getAddress as toChecksumAddress } from "viem";
import { describe, expect, it } from "vitest";
import {
  AssetCatalog,
  AssetId,
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
  getAddress,
  getAsset,
  getAssetByAddress,
  knownPaymentTokens,
  listAssets,
  parseAssetId,
  resolveAssetAlias,
  toAssetAtomic,
} from "../src/networks/index.js";

function snapshot() {
  return [BSC_MAINNET_CHAIN_ID, BSC_TESTNET_CHAIN_ID].flatMap((chainId) =>
    listAssets(chainId).map((asset) => ({
      chainId: asset.chainId,
      assetId: asset.assetId,
      symbol: asset.symbol,
      address: asset.address,
      decimals: asset.decimals,
      b402Methods: [...asset.b402Methods],
      eip3009Domain: asset.eip3009Domain ?? null,
      isDefault: asset.isDefault,
    })),
  );
}

describe("asset catalog", () => {
  it("matches the locked BSC mainnet/testnet snapshot", () => {
    expect(snapshot()).toEqual([
      {
        chainId: 56,
        assetId: "U",
        symbol: "U",
        address: "0xcE24439F2D9C6a2289F741120FE202248B666666",
        decimals: 18,
        b402Methods: ["eip3009", "permit2-exact"],
        eip3009Domain: { name: "United Stables", version: "1" },
        isDefault: true,
      },
      {
        chainId: 56,
        assetId: "BINANCE_PEG_USDC",
        symbol: "USDC",
        address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        decimals: 18,
        b402Methods: ["permit2-exact"],
        eip3009Domain: null,
        isDefault: false,
      },
      {
        chainId: 56,
        assetId: "BINANCE_PEG_USDT",
        symbol: "USDT",
        address: "0x55d398326f99059fF775485246999027B3197955",
        decimals: 18,
        b402Methods: ["permit2-exact"],
        eip3009Domain: null,
        isDefault: false,
      },
      {
        chainId: 97,
        assetId: "TEST_U",
        symbol: "U",
        address: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
        decimals: 18,
        b402Methods: ["eip3009"],
        eip3009Domain: { name: "United Stables", version: "1" },
        isDefault: true,
      },
      {
        chainId: 97,
        assetId: "TEST_USDC",
        symbol: "USDC",
        address: "0xEC1C60D64a06896Df296438c12edD14E974FDE47",
        decimals: 6,
        b402Methods: ["permit2-exact"],
        eip3009Domain: null,
        isDefault: false,
      },
      {
        chainId: 97,
        assetId: "TEST_USDT",
        symbol: "USDT",
        address: "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",
        decimals: 18,
        b402Methods: ["permit2-exact"],
        eip3009Domain: null,
        isDefault: false,
      },
    ]);
  });

  it("stores checksummed addresses and reverse-resolves lowercase input", () => {
    for (const row of snapshot()) {
      expect(row.address).toBe(toChecksumAddress(row.address));
      expect(
        getAssetByAddress(row.chainId, row.address.toLowerCase()),
      ).toMatchObject({
        assetId: row.assetId,
        address: row.address,
      });
    }
  });

  it("resolves friendly aliases only with network context", () => {
    expect(resolveAssetAlias(56, "U")).toBe("U");
    expect(resolveAssetAlias(56, "USDC")).toBe("BINANCE_PEG_USDC");
    expect(resolveAssetAlias(56, "USDT")).toBe("BINANCE_PEG_USDT");
    expect(resolveAssetAlias(97, "U")).toBe("TEST_U");
    expect(resolveAssetAlias(97, "USDC")).toBe("TEST_USDC");
    expect(resolveAssetAlias(97, "USDT")).toBe("TEST_USDT");
  });

  it("parses only canonical ids without network context", () => {
    expect(parseAssetId("BINANCE_PEG_USDC")).toBe("BINANCE_PEG_USDC");
    expect(parseAssetId("TEST_USDT")).toBe("TEST_USDT");
    expect(() => parseAssetId("USDC")).toThrow("canonical AssetId");
    expect(() => parseAssetId("USDT")).toThrow("canonical AssetId");
  });

  it("fails closed for unknown chain, alias, canonical id, and address", () => {
    expect(() => listAssets(1)).toThrow("chain_id=1");
    expect(() => resolveAssetAlias(1, "USDC")).toThrow("chain_id=1");
    expect(() => resolveAssetAlias(56, "BUSD")).toThrow("alias");
    expect(() => getAsset(97, AssetId.BINANCE_PEG_USDC)).toThrow(
      "not available",
    );
    expect(() =>
      getAssetByAddress(56, "0x0000000000000000000000000000000000000001"),
    ).toThrow("not registered");
    expect(() =>
      getAssetByAddress(97, "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"),
    ).toThrow("not registered");
  });

  it("rejects duplicate catalog keys and addresses", () => {
    const first = getAsset(56, AssetId.U);

    expect(() => new AssetCatalog([first, first])).toThrow(
      "duplicate catalog key",
    );
    expect(
      () =>
        new AssetCatalog([
          first,
          { ...first, assetId: AssetId.BINANCE_PEG_USDC },
        ]),
    ).toThrow("duplicate catalog address");
  });

  it("converts exact non-negative decimal strings to bigint atomic amounts", () => {
    expect(toAssetAtomic(56, AssetId.U, "0")).toBe(0n);
    expect(toAssetAtomic(56, AssetId.U, "1.000000000000000001")).toBe(
      10n ** 18n + 1n,
    );
    expect(toAssetAtomic(97, AssetId.TEST_USDC, "1.000001")).toBe(1_000_001n);

    for (const invalid of [
      "",
      " 1",
      "1 ",
      "+1",
      "-1",
      ".1",
      "1.",
      "1e-6",
      "1E6",
    ]) {
      expect(() => toAssetAtomic(56, AssetId.U, invalid)).toThrow(
        "decimal amount",
      );
    }
    expect(() => toAssetAtomic(97, AssetId.TEST_USDC, "1.0000001")).toThrow(
      "decimal places",
    );
    const callWithUnknown = toAssetAtomic as (
      chainId: number,
      assetId: string,
      amount: unknown,
    ) => bigint;
    expect(() => callWithUnknown(56, AssetId.U, 1.1)).toThrow("string");
  });

  it("keeps legacy default-token lookup and EIP-3009 allowlist compatible", () => {
    expect(getAddress(56).paymentToken).toBe(getAsset(56, AssetId.U).address);
    expect(getAddress(97).paymentToken).toBe(
      getAsset(97, AssetId.TEST_U).address,
    );

    expect([...knownPaymentTokens()].sort()).toEqual(
      [
        "56:0xcE24439F2D9C6a2289F741120FE202248B666666",
        "97:0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
      ].sort(),
    );
  });
});

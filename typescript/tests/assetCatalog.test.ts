import { getAddress as toChecksumAddress } from "viem";
import { describe, expect, it } from "vitest";
import {
  AssetCatalog,
  AssetId,
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
  type CatalogPaymentAsset,
  type PaymentAsset,
  getAddress,
  getAsset,
  getAssetByAddress,
  getB402Asset,
  getB402AssetByAddress,
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
      availability: asset.availability,
      b402Methods: [...asset.b402Methods],
      b402Kinds: asset.b402Kinds.map((kind) => ({ ...kind })),
      eip3009Domain: asset.eip3009Domain ?? null,
      isDefault: asset.isDefault,
    })),
  );
}

function kindAt(asset: CatalogPaymentAsset, index: number) {
  const kind = asset.b402Kinds[index];
  if (kind === undefined) {
    throw new Error(`missing fixture B402 kind at index ${index}`);
  }
  return kind;
}

describe("asset catalog", () => {
  it("keeps Testnet U identity while separating ERC-8183 and B402 facts", () => {
    expect(getAsset(97, AssetId.TEST_U)).toMatchObject({
      assetId: AssetId.TEST_U,
      address: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
      decimals: 18,
    });
    expect(getB402Asset(97, AssetId.TEST_U)).toMatchObject({
      assetId: AssetId.TEST_U,
      address: "0x330949Aed7d00FCe0558C64ED6FeC9792616cC39",
      decimals: 6,
    });
    expect(() =>
      getB402AssetByAddress(97, "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"),
    ).toThrow("not registered");
    expect(() =>
      getAssetByAddress(97, "0x330949Aed7d00FCe0558C64ED6FeC9792616cC39"),
    ).toThrow("not registered");
  });

  it("keeps legacy PaymentAsset literals source-compatible while returns stay complete", () => {
    const legacy: PaymentAsset = {
      chainId: 56,
      assetId: AssetId.U,
      symbol: "U",
      address: "0xcE24439F2D9C6a2289F741120FE202248B666666",
      decimals: 18,
      b402Methods: ["eip3009", "permit2-exact"],
      eip3009Domain: { name: "United Stables", version: "1" },
      isDefault: true,
    };
    expect(() => new AssetCatalog([legacy])).toThrow("missing B402 kind");

    const resolved: CatalogPaymentAsset = getAsset(56, AssetId.U);
    const listed: readonly CatalogPaymentAsset[] = listAssets(56);
    expect(resolved.b402Kinds.length).toBe(2);
    expect(listed.every((asset) => asset.b402Kinds.length > 0)).toBe(true);

    const compatibilityCatalog = new AssetCatalog([
      { ...resolved, availability: undefined },
    ]);
    expect(compatibilityCatalog.get(56, AssetId.U).availability).toBe("active");
  });

  it("matches the locked BSC mainnet/testnet snapshot", () => {
    expect(snapshot()).toEqual([
      {
        chainId: 56,
        assetId: "U",
        symbol: "U",
        address: "0xcE24439F2D9C6a2289F741120FE202248B666666",
        decimals: 18,
        availability: "active",
        b402Methods: ["eip3009", "permit2-exact"],
        b402Kinds: [
          { method: "eip3009", name: "United Stables", version: "1" },
          {
            method: "permit2-exact",
            name: "United Stables",
            version: "1",
          },
        ],
        eip3009Domain: { name: "United Stables", version: "1" },
        isDefault: true,
      },
      {
        chainId: 56,
        assetId: "USD1",
        symbol: "USD1",
        address: "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
        decimals: 18,
        availability: "active",
        b402Methods: ["eip3009"],
        b402Kinds: [
          {
            method: "eip3009",
            name: "World Liberty Financial USD",
            version: "1",
          },
        ],
        eip3009Domain: { name: "World Liberty Financial USD", version: "1" },
        isDefault: false,
      },
      {
        chainId: 56,
        assetId: "BINANCE_PEG_USDC",
        symbol: "USDC",
        address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        decimals: 18,
        availability: "active",
        b402Methods: ["permit2-exact"],
        b402Kinds: [
          { method: "permit2-exact", name: "USD Coin", version: "1" },
        ],
        eip3009Domain: null,
        isDefault: false,
      },
      {
        chainId: 56,
        assetId: "BINANCE_PEG_USDT",
        symbol: "USDT",
        address: "0x55d398326f99059fF775485246999027B3197955",
        decimals: 18,
        availability: "active",
        b402Methods: ["permit2-exact"],
        b402Kinds: [
          { method: "permit2-exact", name: "Tether USD", version: "1" },
        ],
        eip3009Domain: null,
        isDefault: false,
      },
      {
        chainId: 97,
        assetId: "TEST_U",
        symbol: "U",
        address: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
        decimals: 18,
        availability: "active",
        b402Methods: ["eip3009"],
        b402Kinds: [{ method: "eip3009", name: "U", version: "1" }],
        eip3009Domain: { name: "U", version: "1" },
        isDefault: true,
      },
      {
        chainId: 97,
        assetId: "TEST_USDC",
        symbol: "USDC",
        address: "0xEC1C60D64a06896Df296438c12edD14E974FDE47",
        decimals: 6,
        availability: "active",
        b402Methods: ["permit2-exact"],
        b402Kinds: [
          { method: "permit2-exact", name: "USD Coin", version: "1" },
        ],
        eip3009Domain: null,
        isDefault: false,
      },
      {
        chainId: 97,
        assetId: "TEST_USDT",
        symbol: "USDT",
        address: "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",
        decimals: 18,
        availability: "active",
        b402Methods: ["permit2-exact"],
        b402Kinds: [
          { method: "permit2-exact", name: "USDT Token", version: "1" },
        ],
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

  it("deep-freezes each B402 kind identity", () => {
    const first = getAsset(56, AssetId.U);

    expect(Object.isFrozen(first.b402Kinds)).toBe(true);
    expect(first.b402Kinds.every((kind) => Object.isFrozen(kind))).toBe(true);
  });

  it("resolves friendly aliases only with network context", () => {
    expect(resolveAssetAlias(56, "U")).toBe("U");
    expect(resolveAssetAlias(56, "USDC")).toBe("BINANCE_PEG_USDC");
    expect(resolveAssetAlias(56, "USDT")).toBe("BINANCE_PEG_USDT");
    expect(resolveAssetAlias(97, "U")).toBe("TEST_U");
    expect(resolveAssetAlias(97, "USDC")).toBe("TEST_USDC");
    expect(resolveAssetAlias(97, "USDT")).toBe("TEST_USDT");
    expect(() => resolveAssetAlias(97, "USD1")).toThrow("unknown asset alias");
  });

  it("exposes mainnet USD1 while testnet USD1 is unsupported", () => {
    expect(getAsset(56, AssetId.USD1)).toMatchObject({
      assetId: "USD1",
      symbol: "USD1",
      address: "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
      decimals: 18,
      availability: "active",
      b402Methods: ["eip3009"],
      eip3009Domain: { name: "World Liberty Financial USD", version: "1" },
    });
    expect(() => parseAssetId("TEST_USD1")).toThrow("canonical AssetId");
    expect(() => resolveAssetAlias(97, "USD1")).toThrow("unknown asset alias");
    expect(() =>
      getAssetByAddress(97, "0x0000000000000000000000000000000000000000"),
    ).toThrow();
  });

  it.each([
    [
      "active assets cannot use the zero address",
      (active: CatalogPaymentAsset) => ({
        ...active,
        address: "0x0000000000000000000000000000000000000000" as const,
      }),
      "active asset cannot use zero address",
    ],
    [
      "placeholders cannot use a nonzero address",
      (active: CatalogPaymentAsset) => ({
        ...active,
        availability: "placeholder" as const,
      }),
      "placeholder asset must use zero address",
    ],
    [
      "placeholders cannot declare B402 methods",
      (active: CatalogPaymentAsset) => ({
        ...active,
        address: "0x0000000000000000000000000000000000000000" as const,
        availability: "placeholder" as const,
        b402Kinds: [],
      }),
      "placeholder asset cannot declare B402 methods",
    ],
    [
      "placeholders cannot declare an EIP-3009 domain",
      (active: CatalogPaymentAsset) => ({
        ...active,
        address: "0x0000000000000000000000000000000000000000" as const,
        availability: "placeholder" as const,
        b402Methods: [],
        b402Kinds: [],
      }),
      "placeholder asset cannot declare an EIP-3009 domain",
    ],
    [
      "placeholders cannot be default assets",
      (active: CatalogPaymentAsset) => ({
        ...active,
        address: "0x0000000000000000000000000000000000000000" as const,
        availability: "placeholder" as const,
        b402Methods: [],
        b402Kinds: [],
        eip3009Domain: null,
      }),
      "placeholder asset cannot be the default asset",
    ],
  ] as const)(
    "rejects invalid availability invariant: %s",
    (_name, fixture, message) => {
      expect(
        () => new AssetCatalog([fixture(getAsset(56, AssetId.U))]),
      ).toThrow(message);
    },
  );

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

  it("rejects non-canonical ids and non-checksummed addresses", () => {
    const first = getAsset(56, AssetId.U);

    expect(
      () =>
        new AssetCatalog([
          { ...first, assetId: "NOT_CANONICAL" } as unknown as PaymentAsset,
        ]),
    ).toThrow("canonical AssetId");
    expect(
      () =>
        new AssetCatalog([
          {
            ...first,
            address: first.address.toLowerCase() as `0x${string}`,
          },
        ]),
    ).toThrow("not checksummed");
  });

  it("requires exactly one kind identity per B402 method", () => {
    const first = getAsset(56, AssetId.U);

    expect(
      () => new AssetCatalog([{ ...first, b402Kinds: [kindAt(first, 0)] }]),
    ).toThrow("missing B402 kind");
    expect(
      () =>
        new AssetCatalog([
          { ...first, b402Kinds: [...first.b402Kinds, ...first.b402Kinds] },
        ]),
    ).toThrow("duplicate B402 kind");
    expect(
      () =>
        new AssetCatalog([
          {
            ...first,
            b402Methods: ["eip3009"],
            b402Kinds: [kindAt(first, 0), kindAt(first, 1)],
          },
        ]),
    ).toThrow("extra B402 kind");
  });

  it("rejects duplicate methods and EIP-3009/domain identity mismatch", () => {
    const first = getAsset(56, AssetId.U);

    expect(
      () =>
        new AssetCatalog([
          {
            ...first,
            b402Methods: ["eip3009", "eip3009"],
            b402Kinds: [kindAt(first, 0)],
          },
        ]),
    ).toThrow("duplicate B402 method");
    expect(
      () =>
        new AssetCatalog([
          {
            ...first,
            b402Kinds: [
              { method: "eip3009", name: "Wrong Token", version: "1" },
              kindAt(first, 1),
            ],
          },
        ]),
    ).toThrow("EIP-3009 kind must match");
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

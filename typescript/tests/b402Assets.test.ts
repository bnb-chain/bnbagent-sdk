import { describe, expect, it } from "vitest";
import {
  AssetId,
  getAsset,
  knownEip3009PaymentTokens,
} from "../src/networks/assets.js";
import {
  UnsupportedWalletRouteError,
  X402SignerError,
  expectedAssetFromPaymentOption,
  paymentOptionFromCli,
  requireB402WalletRoute,
  resolveB402Asset,
} from "../src/x402/index.js";

describe("resolveB402Asset", () => {
  it.each([
    ["eip155:56", AssetId.U, "U", 18, ["eip3009", "permit2-exact"], true],
    [56, AssetId.BINANCE_PEG_USDC, "USDC", 18, ["permit2-exact"], false],
    [56, AssetId.BINANCE_PEG_USDT, "USDT", 18, ["permit2-exact"], false],
    ["eip155:97", AssetId.TEST_U, "U", 18, ["eip3009"], true],
    [97, AssetId.TEST_USDC, "USDC", 6, ["permit2-exact"], false],
    [97, AssetId.TEST_USDT, "USDT", 18, ["permit2-exact"], false],
  ] as const)(
    "resolves %s / %s to the catalog snapshot",
    (network, assetId, symbol, decimals, methods, isDefault) => {
      const expected = resolveB402Asset(network, assetId);
      const catalog = getAsset(expected.chainId, assetId);
      expect(expected).toEqual({
        network: `eip155:${catalog.chainId}`,
        chainId: catalog.chainId,
        assetId,
        symbol,
        address: catalog.address,
        decimals,
        b402Methods: methods,
        b402Kinds: catalog.b402Kinds,
        eip3009Domain: catalog.eip3009Domain,
        isDefault,
      });
      expect(Object.isFrozen(expected)).toBe(true);
    },
  );

  it.each([56, 97])(
    "accepts only same-chain catalog assets on %i",
    (chainId) => {
      for (const assetId of Object.values(AssetId)) {
        try {
          const catalog = getAsset(chainId, assetId);
          expect(resolveB402Asset(chainId, catalog.address).assetId).toBe(
            assetId,
          );
        } catch (error) {
          expect(() => resolveB402Asset(chainId, assetId)).toThrow(
            /(?:not )?available/,
          );
          expect(error).toBeInstanceOf(Error);
        }
      }
    },
  );

  it.each(["56", "bsc", "eip155:1", "eip155:056", 1, true] as const)(
    "rejects unknown or malformed network %s",
    (network) => {
      expect(() =>
        resolveB402Asset(network as string | number, AssetId.U),
      ).toThrow();
    },
  );

  it.each(["USDC", "USDT", "0x1234", "not-an-asset"])(
    "rejects bare or malformed asset %s",
    (asset) => {
      expect(() => resolveB402Asset(56, asset)).toThrow();
    },
  );

  it("requires checksum address input", () => {
    const token = getAsset(97, AssetId.TEST_USDC);
    expect(() => resolveB402Asset(97, token.address.toLowerCase())).toThrow(
      "checksum",
    );
  });

  it("rejects a cross-chain address", () => {
    const mainnetUsdc = getAsset(56, AssetId.BINANCE_PEG_USDC);
    expect(() => resolveB402Asset(97, mainnetUsdc.address)).toThrow(
      "not registered",
    );
  });
});

describe("requireB402WalletRoute", () => {
  it.each([56, 97])(
    "allows evm-local and turnkey only on verified known EIP-3009 U (%i)",
    (chainId) => {
      const assetId = chainId === 56 ? AssetId.U : AssetId.TEST_U;
      const expected = resolveB402Asset(chainId, assetId);
      for (const walletKind of ["evm-local", "turnkey"] as const) {
        expect(requireB402WalletRoute(walletKind, expected, "eip3009")).toEqual(
          {
            walletKind,
            expectedAsset: expected,
            transferMethod: "eip3009",
            delegated: false,
          },
        );
      }
    },
  );

  it.each(["evm-local", "turnkey"] as const)(
    "enables active USD1 EIP-3009 locally for %s while keeping Permit2 and the test placeholder closed",
    (walletKind) => {
      const usd1 = resolveB402Asset("eip155:56", AssetId.USD1);

      expect([...knownEip3009PaymentTokens()].sort()).toEqual(
        [
          `56:${getAsset(56, AssetId.U).address}`,
          `56:${usd1.address}`,
          `97:${getAsset(97, AssetId.TEST_U).address}`,
        ].sort(),
      );

      expect(requireB402WalletRoute(walletKind, usd1, "eip3009")).toMatchObject(
        {
          walletKind,
          transferMethod: "eip3009",
          delegated: false,
        },
      );
      expect(() =>
        requireB402WalletRoute(walletKind, usd1, "permit2-exact"),
      ).toThrow(UnsupportedWalletRouteError);
      expect(() => resolveB402Asset("eip155:97", AssetId.TEST_USD1)).toThrow(
        "unavailable",
      );
    },
  );

  it.each([
    [56, AssetId.BINANCE_PEG_USDC],
    [56, AssetId.BINANCE_PEG_USDT],
    [97, AssetId.TEST_USDC],
    [97, AssetId.TEST_USDT],
  ] as const)(
    "returns typed unsupported for local Permit2-only route %i / %s",
    (chainId, assetId) => {
      const expected = resolveB402Asset(chainId, assetId);
      for (const walletKind of ["evm-local", "turnkey"] as const) {
        let caught: unknown;
        try {
          requireB402WalletRoute(walletKind, expected, "permit2-exact");
        } catch (error) {
          caught = error;
        }
        expect(caught).toBeInstanceOf(UnsupportedWalletRouteError);
        expect(caught).toBeInstanceOf(X402SignerError);
        const typed = caught as UnsupportedWalletRouteError;
        expect({
          walletKind: typed.walletKind,
          network: typed.network,
          chainId: typed.chainId,
          assetId: typed.assetId,
          transferMethod: typed.transferMethod,
        }).toEqual({
          walletKind,
          network: `eip155:${chainId}`,
          chainId,
          assetId,
          transferMethod: "permit2-exact",
        });
        expect(typed.message).toContain(walletKind);
        expect(typed.message).toContain(`eip155:${chainId}`);
        expect(typed.message).toContain(assetId);
        expect(typed.message).toContain("permit2-exact");
      }
    },
  );

  it.each(["evm-local", "turnkey"] as const)(
    "does not open Permit2 for local wallet %s even when U declares it",
    (walletKind) => {
      const expected = resolveB402Asset(56, AssetId.U);
      expect(() =>
        requireB402WalletRoute(walletKind, expected, "permit2-exact"),
      ).toThrow(UnsupportedWalletRouteError);
    },
  );

  it.each(["twak", "altana"] as const)(
    "allows catalog-declared delegated routes for %s",
    (walletKind) => {
      const routes = [
        [56, AssetId.U, "eip3009"],
        [56, AssetId.U, "permit2-exact"],
        [56, AssetId.BINANCE_PEG_USDC, "permit2-exact"],
        [56, AssetId.BINANCE_PEG_USDT, "permit2-exact"],
        [97, AssetId.TEST_U, "eip3009"],
        [97, AssetId.TEST_USDC, "permit2-exact"],
        [97, AssetId.TEST_USDT, "permit2-exact"],
      ] as const;
      for (const [chainId, assetId, transferMethod] of routes) {
        const expected = resolveB402Asset(chainId, assetId);
        expect(
          requireB402WalletRoute(walletKind, expected, transferMethod),
        ).toEqual({
          walletKind,
          expectedAsset: expected,
          transferMethod,
          delegated: true,
        });
      }
    },
  );

  it.each(["twak", "altana"] as const)(
    "rejects methods missing from each asset catalog entry for %s",
    (walletKind) => {
      const routes = [
        [56, AssetId.BINANCE_PEG_USDC, "eip3009"],
        [56, AssetId.BINANCE_PEG_USDT, "eip3009"],
        [97, AssetId.TEST_U, "permit2-exact"],
      ] as const;
      for (const [chainId, assetId, transferMethod] of routes) {
        const expected = resolveB402Asset(chainId, assetId);
        expect(() =>
          requireB402WalletRoute(walletKind, expected, transferMethod),
        ).toThrow(UnsupportedWalletRouteError);
      }
    },
  );

  it.each(["evm-local", "turnkey", "twak", "altana"] as const)(
    "never falls back from the expected asset for %s",
    (walletKind) => {
      const expected = resolveB402Asset(97, AssetId.TEST_USDC);
      for (const transferMethod of [
        "permit2-upto",
        "permit2",
        "unknown",
      ] as const) {
        expect(() =>
          requireB402WalletRoute(walletKind, expected, transferMethod),
        ).toThrow(UnsupportedWalletRouteError);
        try {
          requireB402WalletRoute(walletKind, expected, transferMethod);
        } catch (error) {
          const typed = error as UnsupportedWalletRouteError;
          expect(typed.assetId).toBe(AssetId.TEST_USDC);
          expect(typed.transferMethod).toBe(transferMethod);
        }
      }
    },
  );

  it("returns typed unsupported for an unknown wallet", () => {
    const expected = resolveB402Asset(56, AssetId.U);
    expect(() => requireB402WalletRoute("mpc", expected, "eip3009")).toThrow(
      UnsupportedWalletRouteError,
    );
  });

  it("rejects manually forged expected metadata", () => {
    const expected = resolveB402Asset(56, AssetId.U);
    let caught: unknown;
    try {
      requireB402WalletRoute(
        "evm-local",
        { ...expected, symbol: "USDC" },
        "eip3009",
      );
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(UnsupportedWalletRouteError);
    expect((caught as UnsupportedWalletRouteError).assetId).toBe(AssetId.U);
    expect((caught as UnsupportedWalletRouteError).network).toBe("eip155:56");
  });

  it("returns immutable catalog metadata instead of a mutable input clone", () => {
    const expected = resolveB402Asset(56, AssetId.U);
    const mutable = {
      ...expected,
      b402Methods: [...expected.b402Methods],
      b402Kinds: expected.b402Kinds.map((kind) => ({ ...kind })),
      eip3009Domain:
        expected.eip3009Domain === null ? null : { ...expected.eip3009Domain },
    };

    const route = requireB402WalletRoute("evm-local", mutable, "eip3009");
    mutable.address = getAsset(56, AssetId.BINANCE_PEG_USDC).address;
    mutable.b402Methods.length = 0;
    const mutableKind = mutable.b402Kinds[0];
    if (mutableKind === undefined) {
      throw new Error("missing fixture B402 kind");
    }
    mutableKind.name = "Forged Token";

    expect(route.expectedAsset).toEqual(expected);
    expect(route.expectedAsset).not.toBe(mutable);
    expect(route.expectedAsset.address).toBe(expected.address);
    expect(route.expectedAsset.b402Methods).toEqual(expected.b402Methods);
    expect(route.expectedAsset.b402Kinds).toEqual(expected.b402Kinds);
    expect(Object.isFrozen(route.expectedAsset)).toBe(true);
  });

  it("returns canonical metadata for a fabricated structural identity", () => {
    const expected = resolveB402Asset(56, AssetId.U);
    const fabricated = {
      isDefault: expected.isDefault,
      eip3009Domain: expected.eip3009Domain,
      b402Methods: expected.b402Methods,
      b402Kinds: expected.b402Kinds,
      decimals: expected.decimals,
      address: expected.address,
      symbol: expected.symbol,
      assetId: "U" as typeof AssetId.U,
      chainId: expected.chainId,
      network: expected.network,
    };

    const route = requireB402WalletRoute("evm-local", fabricated, "eip3009");

    expect(route.expectedAsset).toEqual(expected);
    expect(route.expectedAsset).not.toBe(fabricated);
    expect(Object.isFrozen(route.expectedAsset)).toBe(true);
  });

  it("rejects forged B402 kind identity metadata", () => {
    const expected = resolveB402Asset(97, AssetId.TEST_USDT);
    const originalKind = expected.b402Kinds[0];
    if (originalKind === undefined) {
      throw new Error("missing fixture B402 kind");
    }
    const forged = {
      ...expected,
      b402Kinds: [{ ...originalKind, name: "Tether USD" }],
    };

    expect(() =>
      requireB402WalletRoute("altana", forged, "permit2-exact"),
    ).toThrow(UnsupportedWalletRouteError);
  });
});

it("derives the same expected asset from a payment option and keeps bigint amount", () => {
  const token = getAsset(97, AssetId.TEST_USDC);
  const option = paymentOptionFromCli({
    network: "eip155:97",
    asset: token.address,
    amount: "1000001",
    payTo: "0x0000000000000000000000000000000000000001",
    transferMethod: "permit2-exact",
  });

  const expected = expectedAssetFromPaymentOption(option);

  expect(expected.assetId).toBe(AssetId.TEST_USDC);
  expect(expected.decimals).toBe(6);
  expect(option.amount).toBe(1_000_001n);
  expect(typeof option.amount).toBe("bigint");
});

/** Canonical payment-asset catalog for supported BNB Chain networks. */

import { getAddress as toChecksumAddress } from "viem";
import {
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
  PAYMENT_TOKEN_EIP712_NAME,
  PAYMENT_TOKEN_EIP712_VERSION,
} from "./addresses.js";

export const AssetId = Object.freeze({
  U: "U",
  TEST_U: "TEST_U",
  USD1: "USD1",
  TEST_USD1: "TEST_USD1",
  BINANCE_PEG_USDC: "BINANCE_PEG_USDC",
  BINANCE_PEG_USDT: "BINANCE_PEG_USDT",
  TEST_USDC: "TEST_USDC",
  TEST_USDT: "TEST_USDT",
} as const);

export type AssetId = (typeof AssetId)[keyof typeof AssetId];
export type AssetAlias = "U" | "USD1" | "USDC" | "USDT";
export type B402TransferMethod = "eip3009" | "permit2-exact";
export type PaymentAssetAvailability = "active" | "placeholder";

export interface EIP3009Domain {
  readonly name: string;
  readonly version: string;
}

export interface B402Kind {
  readonly method: B402TransferMethod;
  readonly name: string;
  readonly version: string;
}

export interface PaymentAsset {
  readonly chainId: number;
  readonly assetId: AssetId;
  readonly symbol: string;
  readonly address: `0x${string}`;
  readonly decimals: number;
  /** Omitted availability preserves the legacy active-asset behavior. */
  readonly availability?: PaymentAssetAvailability;
  readonly b402Methods: readonly B402TransferMethod[];
  /**
   * Optional for source compatibility with pre-kind object literals.
   * AssetCatalog rejects omission and returns CatalogPaymentAsset instead.
   */
  readonly b402Kinds?: readonly B402Kind[];
  readonly eip3009Domain: EIP3009Domain | null;
  readonly isDefault: boolean;
}

/** A catalog-validated asset with complete B402 kind identities. */
export interface CatalogPaymentAsset extends PaymentAsset {
  readonly availability: PaymentAssetAvailability;
  readonly b402Kinds: readonly B402Kind[];
}

export class PaymentAssetUnavailableError extends Error {
  readonly chainId: number;
  readonly assetId: AssetId;

  constructor(chainId: number, assetId: AssetId) {
    super(`AssetId ${assetId} is unavailable on chain_id=${chainId}`);
    this.name = "PaymentAssetUnavailableError";
    this.chainId = chainId;
    this.assetId = assetId;
  }
}

const CANONICAL_IDS = new Set<string>(Object.values(AssetId));

export function parseAssetId(value: string): AssetId {
  if (!CANONICAL_IDS.has(value)) {
    throw new Error(`unknown canonical AssetId: ${JSON.stringify(value)}`);
  }
  return value as AssetId;
}

/** Fail-closed lookup index keyed by network, canonical id, and address. */
export class AssetCatalog {
  readonly #metadataByKey = new Map<string, CatalogPaymentAsset>();
  readonly #metadataByAddress = new Map<string, CatalogPaymentAsset>();
  readonly #activeByKey = new Map<string, CatalogPaymentAsset>();
  readonly #activeByAddress = new Map<string, CatalogPaymentAsset>();
  readonly #activeByChain = new Map<number, readonly CatalogPaymentAsset[]>();
  readonly #metadataChains = new Set<number>();

  constructor(assets: readonly PaymentAsset[]) {
    const mutableActiveByChain = new Map<number, CatalogPaymentAsset[]>();

    for (const input of assets) {
      const assetId = parseAssetId(input.assetId);
      const key = `${input.chainId}:${assetId}`;
      if (this.#metadataByKey.has(key)) {
        throw new Error(
          `duplicate catalog key: chain_id=${input.chainId}, asset_id=${assetId}`,
        );
      }

      const address = toChecksumAddress(input.address);
      if (address !== input.address) {
        throw new Error(`catalog address is not checksummed: ${input.address}`);
      }

      const availability = input.availability ?? "active";
      validateAvailability(input, availability, address);
      const b402Kinds = validateB402Metadata(input);
      const addressKey = `${input.chainId}:${address.toLowerCase()}`;
      if (this.#metadataByAddress.has(addressKey)) {
        throw new Error(
          `duplicate catalog address: chain_id=${input.chainId}, address=${address}`,
        );
      }

      const asset: CatalogPaymentAsset = Object.freeze({
        ...input,
        assetId,
        address,
        availability,
        b402Methods: Object.freeze([...input.b402Methods]),
        b402Kinds: Object.freeze(
          b402Kinds.map((kind) => Object.freeze({ ...kind })),
        ),
        eip3009Domain:
          input.eip3009Domain === null
            ? null
            : Object.freeze({ ...input.eip3009Domain }),
      });
      this.#metadataByKey.set(key, asset);
      this.#metadataByAddress.set(addressKey, asset);
      this.#metadataChains.add(asset.chainId);
      if (asset.availability === "active") {
        this.#activeByKey.set(key, asset);
        this.#activeByAddress.set(addressKey, asset);
        const chainAssets = mutableActiveByChain.get(asset.chainId) ?? [];
        chainAssets.push(asset);
        mutableActiveByChain.set(asset.chainId, chainAssets);
      }
    }

    for (const chainId of this.#metadataChains) {
      this.#activeByChain.set(
        chainId,
        Object.freeze([...(mutableActiveByChain.get(chainId) ?? [])]),
      );
    }
    Object.freeze(this);
  }

  #requireChain(chainId: number): void {
    if (!this.#metadataChains.has(chainId)) {
      throw new Error(`no asset catalog registered for chain_id=${chainId}`);
    }
  }

  get(chainId: number, assetId: AssetId | string): CatalogPaymentAsset {
    const asset = this.getMetadata(chainId, assetId);
    if (asset.availability === "placeholder") {
      throw new PaymentAssetUnavailableError(chainId, asset.assetId);
    }
    const activeAsset = this.#activeByKey.get(`${chainId}:${asset.assetId}`);
    if (activeAsset === undefined) {
      throw new Error(
        `AssetId ${asset.assetId} is not available on chain_id=${chainId}`,
      );
    }
    return activeAsset;
  }

  getMetadata(chainId: number, assetId: AssetId | string): CatalogPaymentAsset {
    this.#requireChain(chainId);
    const canonical = parseAssetId(assetId);
    const asset = this.#metadataByKey.get(`${chainId}:${canonical}`);
    if (asset === undefined) {
      throw new Error(
        `AssetId ${canonical} is not available on chain_id=${chainId}`,
      );
    }
    return asset;
  }

  byAddress(chainId: number, inputAddress: string): CatalogPaymentAsset {
    this.#requireChain(chainId);
    let address: `0x${string}`;
    try {
      address = toChecksumAddress(inputAddress);
    } catch (error) {
      throw new Error(
        `asset address ${JSON.stringify(inputAddress)} is not registered on chain_id=${chainId}`,
        { cause: error },
      );
    }
    const asset = this.#activeByAddress.get(
      `${chainId}:${address.toLowerCase()}`,
    );
    if (asset === undefined) {
      throw new Error(
        `asset address ${JSON.stringify(inputAddress)} is not registered on chain_id=${chainId}`,
      );
    }
    return asset;
  }

  list(chainId: number): readonly CatalogPaymentAsset[] {
    this.#requireChain(chainId);
    const assets = this.#activeByChain.get(chainId);
    if (assets === undefined) {
      throw new Error(`no asset catalog registered for chain_id=${chainId}`);
    }
    return assets;
  }
}

const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";

function validateAvailability(
  input: PaymentAsset,
  availability: PaymentAssetAvailability,
  address: `0x${string}`,
): void {
  if (availability !== "active" && availability !== "placeholder") {
    throw new Error("unknown payment asset availability");
  }
  if (availability === "active") {
    if (address.toLowerCase() === ZERO_ADDRESS) {
      throw new Error("active asset cannot use zero address");
    }
    return;
  }
  if (address.toLowerCase() !== ZERO_ADDRESS) {
    throw new Error("placeholder asset must use zero address");
  }
  if (input.b402Methods.length > 0 || (input.b402Kinds?.length ?? 0) > 0) {
    throw new Error("placeholder asset cannot declare B402 methods");
  }
  if (input.eip3009Domain !== null) {
    throw new Error("placeholder asset cannot declare an EIP-3009 domain");
  }
  if (input.isDefault) {
    throw new Error("placeholder asset cannot be the default asset");
  }
}

const B402_TRANSFER_METHODS = new Set<B402TransferMethod>([
  "eip3009",
  "permit2-exact",
]);

function validateB402Metadata(input: PaymentAsset): readonly B402Kind[] {
  const methods = input.b402Methods;
  if (new Set(methods).size !== methods.length) {
    throw new Error("duplicate B402 method in asset catalog");
  }
  if (methods.some((method) => !B402_TRANSFER_METHODS.has(method))) {
    throw new Error("unknown B402 method in asset catalog");
  }

  const kinds = input.b402Kinds ?? [];
  const kindMethods = kinds.map((kind) => kind.method);
  if (new Set(kindMethods).size !== kindMethods.length) {
    throw new Error("duplicate B402 kind identity in asset catalog");
  }
  if (kindMethods.some((method) => !B402_TRANSFER_METHODS.has(method))) {
    throw new Error("unknown B402 kind method in asset catalog");
  }
  if (
    kinds.some((kind) => kind.name.length === 0 || kind.version.length === 0)
  ) {
    throw new Error("B402 kind name and version must be non-empty");
  }

  const missing = methods.find((method) => !kindMethods.includes(method));
  if (missing !== undefined) {
    throw new Error(`missing B402 kind identity for method=${missing}`);
  }
  const extra = kindMethods.find((method) => !methods.includes(method));
  if (extra !== undefined) {
    throw new Error(`extra B402 kind identity for method=${extra}`);
  }

  const eip3009Kind = kinds.find((kind) => kind.method === "eip3009");
  if (eip3009Kind === undefined) {
    if (input.eip3009Domain !== null) {
      throw new Error("EIP-3009 domain requires an eip3009 B402 method");
    }
  } else if (
    input.eip3009Domain === null ||
    eip3009Kind.name !== input.eip3009Domain.name ||
    eip3009Kind.version !== input.eip3009Domain.version
  ) {
    throw new Error("EIP-3009 kind must match eip3009Domain exactly");
  }
  return kinds;
}

const EIP3009_DOMAIN = Object.freeze({
  name: PAYMENT_TOKEN_EIP712_NAME,
  version: PAYMENT_TOKEN_EIP712_VERSION,
});

export const ASSET_CATALOG = new AssetCatalog([
  {
    chainId: BSC_MAINNET_CHAIN_ID,
    assetId: AssetId.U,
    symbol: "U",
    address: "0xcE24439F2D9C6a2289F741120FE202248B666666",
    decimals: 18,
    b402Methods: ["eip3009", "permit2-exact"],
    b402Kinds: [
      { method: "eip3009", name: "United Stables", version: "1" },
      { method: "permit2-exact", name: "United Stables", version: "1" },
    ],
    eip3009Domain: EIP3009_DOMAIN,
    isDefault: true,
  },
  {
    chainId: BSC_MAINNET_CHAIN_ID,
    assetId: AssetId.USD1,
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
    chainId: BSC_MAINNET_CHAIN_ID,
    assetId: AssetId.BINANCE_PEG_USDC,
    symbol: "USDC",
    address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    decimals: 18,
    b402Methods: ["permit2-exact"],
    b402Kinds: [{ method: "permit2-exact", name: "USD Coin", version: "1" }],
    eip3009Domain: null,
    isDefault: false,
  },
  {
    chainId: BSC_MAINNET_CHAIN_ID,
    assetId: AssetId.BINANCE_PEG_USDT,
    symbol: "USDT",
    address: "0x55d398326f99059fF775485246999027B3197955",
    decimals: 18,
    b402Methods: ["permit2-exact"],
    b402Kinds: [{ method: "permit2-exact", name: "Tether USD", version: "1" }],
    eip3009Domain: null,
    isDefault: false,
  },
  {
    chainId: BSC_TESTNET_CHAIN_ID,
    assetId: AssetId.TEST_U,
    symbol: "U",
    address: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
    decimals: 18,
    b402Methods: ["eip3009"],
    b402Kinds: [{ method: "eip3009", name: "United Stables", version: "1" }],
    eip3009Domain: EIP3009_DOMAIN,
    isDefault: true,
  },
  {
    chainId: BSC_TESTNET_CHAIN_ID,
    assetId: AssetId.TEST_USD1,
    symbol: "USD1",
    address: ZERO_ADDRESS,
    decimals: 18,
    availability: "placeholder",
    b402Methods: [],
    b402Kinds: [],
    eip3009Domain: null,
    isDefault: false,
  },
  {
    chainId: BSC_TESTNET_CHAIN_ID,
    assetId: AssetId.TEST_USDC,
    symbol: "USDC",
    address: "0xEC1C60D64a06896Df296438c12edD14E974FDE47",
    decimals: 6,
    b402Methods: ["permit2-exact"],
    b402Kinds: [{ method: "permit2-exact", name: "USD Coin", version: "1" }],
    eip3009Domain: null,
    isDefault: false,
  },
  {
    chainId: BSC_TESTNET_CHAIN_ID,
    assetId: AssetId.TEST_USDT,
    symbol: "USDT",
    address: "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",
    decimals: 18,
    b402Methods: ["permit2-exact"],
    b402Kinds: [{ method: "permit2-exact", name: "USDT Token", version: "1" }],
    eip3009Domain: null,
    isDefault: false,
  },
]);

const ALIASES: Readonly<Record<number, Readonly<Record<AssetAlias, AssetId>>>> =
  Object.freeze({
    [BSC_MAINNET_CHAIN_ID]: Object.freeze({
      U: AssetId.U,
      USD1: AssetId.USD1,
      USDC: AssetId.BINANCE_PEG_USDC,
      USDT: AssetId.BINANCE_PEG_USDT,
    }),
    [BSC_TESTNET_CHAIN_ID]: Object.freeze({
      U: AssetId.TEST_U,
      USD1: AssetId.TEST_USD1,
      USDC: AssetId.TEST_USDC,
      USDT: AssetId.TEST_USDT,
    }),
  });

export function resolveAssetAlias(chainId: number, alias: string): AssetId {
  const aliases = ALIASES[chainId];
  if (aliases === undefined) {
    throw new Error(`no asset catalog registered for chain_id=${chainId}`);
  }
  const resolved = aliases[alias as AssetAlias];
  if (resolved === undefined) {
    throw new Error(
      `unknown asset alias ${JSON.stringify(alias)} for chain_id=${chainId}`,
    );
  }
  return resolved;
}

export function getAsset(
  chainId: number,
  assetId: AssetId | string,
): CatalogPaymentAsset {
  return ASSET_CATALOG.get(chainId, assetId);
}

/** Return validated metadata, including diagnostic placeholder entries. */
export function getAssetMetadata(
  chainId: number,
  assetId: AssetId | string,
): CatalogPaymentAsset {
  return ASSET_CATALOG.getMetadata(chainId, assetId);
}

export function getAssetByAddress(
  chainId: number,
  address: string,
): CatalogPaymentAsset {
  return ASSET_CATALOG.byAddress(chainId, address);
}

export function listAssets(chainId: number): readonly CatalogPaymentAsset[] {
  return ASSET_CATALOG.list(chainId);
}

/**
 * EIP-712 domain keys for active catalog assets verified for EIP-3009.
 *
 * This deliberately derives from the asset catalog instead of the legacy
 * deployment-address registry: only assets declaring both a verified domain
 * and the EIP-3009 B402 route may enter a signing allowlist.
 */
export function knownEip3009PaymentTokens(): ReadonlySet<string> {
  return new Set(
    [BSC_MAINNET_CHAIN_ID, BSC_TESTNET_CHAIN_ID].flatMap((chainId) =>
      listAssets(chainId)
        .filter(
          (asset) =>
            asset.eip3009Domain !== null &&
            asset.b402Methods.includes("eip3009"),
        )
        .map((asset) => `${chainId}:${asset.address}`),
    ),
  );
}

const DECIMAL_AMOUNT = /^[0-9]+(?:\.[0-9]+)?$/;

export function toAssetAtomic(
  chainId: number,
  assetId: AssetId | string,
  amount: string,
): bigint {
  if (typeof amount !== "string") {
    throw new TypeError("asset amount must be a decimal string");
  }
  if (!DECIMAL_AMOUNT.test(amount)) {
    throw new Error(`invalid decimal amount: ${JSON.stringify(amount)}`);
  }

  const asset = getAsset(chainId, assetId);
  const [whole, fraction = ""] = amount.split(".");
  if (fraction.length > asset.decimals) {
    throw new Error(
      `asset amount exceeds ${asset.decimals} decimal places: ${JSON.stringify(amount)}`,
    );
  }
  return (
    BigInt(whole) * 10n ** BigInt(asset.decimals) +
    BigInt(fraction.padEnd(asset.decimals, "0") || "0")
  );
}

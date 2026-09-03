/** Strict B402 expected-asset resolution and wallet-route capabilities. */

import { getAddress as toChecksumAddress } from "viem";
import {
  type AssetId,
  type B402Kind,
  type B402TransferMethod,
  type CatalogPaymentAsset,
  getB402Asset,
  getB402AssetByAddress,
  knownEip3009PaymentTokens,
} from "../networks/assets.js";
import { UnsupportedWalletRouteError } from "./errors.js";

export interface ExpectedB402Asset {
  readonly network: string;
  readonly chainId: number;
  readonly assetId: AssetId;
  readonly symbol: string;
  readonly address: `0x${string}`;
  readonly decimals: number;
  readonly b402Methods: readonly B402TransferMethod[];
  readonly b402Kinds: readonly B402Kind[];
  readonly eip3009Domain: Readonly<{ name: string; version: string }> | null;
  readonly isDefault: boolean;
}

export interface B402WalletRoute {
  readonly walletKind: string;
  readonly expectedAsset: ExpectedB402Asset;
  readonly transferMethod: B402TransferMethod;
  readonly delegated: boolean;
}

/**
 * The minimal capability contract for a delegated exact payer. A wallet kind
 * is not enough evidence that a particular transfer rail is usable: the
 * concrete payer must expose an atomic exact-request operation and advertise
 * the requested method itself.
 */
export interface DelegatedX402ExactPayerCapability {
  readonly exactTransferMethods?: readonly B402TransferMethod[];
  readonly requestExact?: unknown;
}

/**
 * A caller-selected EIP-3009 route resolved from the SDK's immutable asset
 * catalog. It is the trust anchor for local x402 signing: the challenge may
 * supply typed-data, but it may not select a token or EIP-712 domain.
 */
export interface ExpectedEip3009Route {
  readonly network: `eip155:${number}`;
  readonly chainId: number;
  readonly assetId: AssetId;
  readonly address: `0x${string}`;
  readonly transferMethod: "eip3009";
  readonly name: string;
  readonly version: string;
}

const CAIP2_NETWORK = /^eip155:(56|97)$/;
const LOCAL_WALLETS = new Set(["evm-local", "turnkey"]);
const DELEGATED_WALLETS = new Set(["twak", "altana"]);

function parseNetwork(network: string | number): number {
  if (typeof network === "number") {
    if (!Number.isSafeInteger(network)) {
      throw new TypeError("B402 network chain id must be a safe integer");
    }
    return network;
  }
  if (typeof network !== "string") {
    throw new TypeError(
      "B402 network must be a chain id or CAIP-2 eip155 network",
    );
  }
  const matched = CAIP2_NETWORK.exec(network);
  if (matched?.[1] === undefined) {
    throw new Error(
      `unsupported or malformed B402 network: ${JSON.stringify(network)}`,
    );
  }
  return Number(matched[1]);
}

/** Resolve a canonical AssetId or an already-checksummed address. */
export function resolveB402Asset(
  network: string | number,
  asset: AssetId | string,
): ExpectedB402Asset {
  const chainId = parseNetwork(network);
  let catalog: CatalogPaymentAsset;
  if (asset.startsWith("0x")) {
    let checksummed: `0x${string}`;
    try {
      checksummed = toChecksumAddress(asset);
    } catch (error) {
      throw new Error(
        `B402 asset address must be checksummed: ${JSON.stringify(asset)}`,
        {
          cause: error,
        },
      );
    }
    if (checksummed !== asset) {
      throw new Error(
        `B402 asset address must be checksummed: ${JSON.stringify(asset)}`,
      );
    }
    catalog = getB402AssetByAddress(chainId, asset);
  } else {
    catalog = getB402Asset(chainId, asset);
  }

  return Object.freeze({
    network: `eip155:${chainId}`,
    chainId,
    assetId: catalog.assetId,
    symbol: catalog.symbol,
    address: catalog.address,
    decimals: catalog.decimals,
    b402Methods: catalog.b402Methods,
    b402Kinds: catalog.b402Kinds,
    eip3009Domain: catalog.eip3009Domain,
    isDefault: catalog.isDefault,
  });
}

/** Resolve one active catalog asset into its exact EIP-3009 signing route. */
export function resolveExpectedEip3009Route(
  network: string | number,
  asset: AssetId | string,
): ExpectedEip3009Route {
  const expectedAsset = resolveB402Asset(network, asset);
  const kind = expectedAsset.b402Kinds.find(
    (candidate) => candidate.method === "eip3009",
  );
  const domain = expectedAsset.eip3009Domain;
  if (
    !expectedAsset.b402Methods.includes("eip3009") ||
    kind === undefined ||
    domain === null ||
    kind.name !== domain.name ||
    kind.version !== domain.version
  ) {
    throw new Error(
      `asset ${expectedAsset.assetId} has no catalog EIP-3009 signing route`,
    );
  }
  return Object.freeze({
    network: expectedAsset.network as `eip155:${number}`,
    chainId: expectedAsset.chainId,
    assetId: expectedAsset.assetId,
    address: expectedAsset.address,
    transferMethod: "eip3009" as const,
    name: domain.name,
    version: domain.version,
  });
}

/**
 * Re-resolve a public route and require an exact catalog-canonical match.
 * Structural typing cannot prove provenance, so an exact clone of resolver
 * output is accepted; stale, placeholder, and field-drifted routes are not.
 */
export function requireExpectedEip3009Route(
  route: ExpectedEip3009Route,
): ExpectedEip3009Route {
  const canonical = resolveExpectedEip3009Route(route.network, route.address);
  if (
    route.network !== canonical.network ||
    route.chainId !== canonical.chainId ||
    route.assetId !== canonical.assetId ||
    route.address !== canonical.address ||
    route.transferMethod !== canonical.transferMethod ||
    route.name !== canonical.name ||
    route.version !== canonical.version
  ) {
    throw new Error("expected EIP-3009 route does not match the asset catalog");
  }
  return canonical;
}

/** Validate a wallet route for the exact expected asset, or throw typed unsupported. */
export function requireB402WalletRoute(
  walletKind: string,
  expectedAsset: ExpectedB402Asset,
  transferMethod: string,
  delegatedPayer?: DelegatedX402ExactPayerCapability,
): B402WalletRoute {
  let addressResolved = true;
  let catalogExpected: ExpectedB402Asset;
  try {
    catalogExpected = resolveB402Asset(
      expectedAsset.network,
      expectedAsset.address,
    );
  } catch {
    addressResolved = false;
    catalogExpected = resolveB402Asset(
      expectedAsset.chainId,
      expectedAsset.assetId,
    );
  }
  const domainMatches =
    catalogExpected.eip3009Domain === null
      ? expectedAsset.eip3009Domain === null
      : expectedAsset.eip3009Domain !== null &&
        catalogExpected.eip3009Domain.name ===
          expectedAsset.eip3009Domain.name &&
        catalogExpected.eip3009Domain.version ===
          expectedAsset.eip3009Domain.version;
  const providedKinds = Array.isArray(expectedAsset.b402Kinds)
    ? expectedAsset.b402Kinds
    : [];
  const catalogMatches =
    addressResolved &&
    catalogExpected.network === expectedAsset.network &&
    catalogExpected.chainId === expectedAsset.chainId &&
    catalogExpected.assetId === expectedAsset.assetId &&
    catalogExpected.symbol === expectedAsset.symbol &&
    catalogExpected.address === expectedAsset.address &&
    catalogExpected.decimals === expectedAsset.decimals &&
    catalogExpected.isDefault === expectedAsset.isDefault &&
    catalogExpected.b402Methods.length === expectedAsset.b402Methods.length &&
    catalogExpected.b402Methods.every(
      (method, index) => method === expectedAsset.b402Methods[index],
    ) &&
    catalogExpected.b402Kinds.length === providedKinds.length &&
    catalogExpected.b402Kinds.every((kind, index) => {
      const provided = providedKinds[index];
      return (
        provided !== undefined &&
        kind.method === provided.method &&
        kind.name === provided.name &&
        kind.version === provided.version
      );
    }) &&
    domainMatches;
  const methodSupported = catalogExpected.b402Methods.includes(
    transferMethod as B402TransferMethod,
  );
  const delegated =
    DELEGATED_WALLETS.has(walletKind) &&
    delegatedPayer !== undefined &&
    typeof delegatedPayer.requestExact === "function" &&
    Array.isArray(delegatedPayer.exactTransferMethods) &&
    delegatedPayer.exactTransferMethods.includes(
      transferMethod as B402TransferMethod,
    );

  const supportedDelegated = catalogMatches && methodSupported && delegated;
  const supportedLocalEip3009 =
    catalogMatches &&
    LOCAL_WALLETS.has(walletKind) &&
    transferMethod === "eip3009" &&
    catalogExpected.eip3009Domain !== null &&
    knownEip3009PaymentTokens().has(
      `${catalogExpected.chainId}:${catalogExpected.address}`,
    );

  if (!supportedDelegated && !supportedLocalEip3009) {
    throw new UnsupportedWalletRouteError({
      walletKind,
      network: catalogExpected.network,
      chainId: catalogExpected.chainId,
      assetId: catalogExpected.assetId,
      transferMethod,
    });
  }

  return Object.freeze({
    walletKind,
    expectedAsset: catalogExpected,
    transferMethod: transferMethod as B402TransferMethod,
    delegated,
  });
}

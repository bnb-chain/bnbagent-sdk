/** Strict B402 expected-asset resolution and wallet-route capabilities. */

import { getAddress as toChecksumAddress } from "viem";
import { knownPaymentTokens } from "../networks/addresses.js";
import {
  type AssetId,
  type B402TransferMethod,
  type PaymentAsset,
  getAsset,
  getAssetByAddress,
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
  readonly eip3009Domain: Readonly<{ name: string; version: string }> | null;
  readonly isDefault: boolean;
}

export interface B402WalletRoute {
  readonly walletKind: string;
  readonly expectedAsset: ExpectedB402Asset;
  readonly transferMethod: B402TransferMethod;
  readonly delegated: boolean;
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
  let catalog: PaymentAsset;
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
    catalog = getAssetByAddress(chainId, asset);
  } else {
    catalog = getAsset(chainId, asset);
  }

  return Object.freeze({
    network: `eip155:${chainId}`,
    chainId,
    assetId: catalog.assetId,
    symbol: catalog.symbol,
    address: catalog.address,
    decimals: catalog.decimals,
    b402Methods: catalog.b402Methods,
    eip3009Domain: catalog.eip3009Domain,
    isDefault: catalog.isDefault,
  });
}

/** Validate a wallet route for the exact expected asset, or throw typed unsupported. */
export function requireB402WalletRoute(
  walletKind: string,
  expectedAsset: ExpectedB402Asset,
  transferMethod: string,
): B402WalletRoute {
  const catalogExpected = resolveB402Asset(
    expectedAsset.network,
    expectedAsset.assetId,
  );
  const domainMatches =
    catalogExpected.eip3009Domain === null
      ? expectedAsset.eip3009Domain === null
      : expectedAsset.eip3009Domain !== null &&
        catalogExpected.eip3009Domain.name ===
          expectedAsset.eip3009Domain.name &&
        catalogExpected.eip3009Domain.version ===
          expectedAsset.eip3009Domain.version;
  const catalogMatches =
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
    domainMatches;
  const methodSupported = expectedAsset.b402Methods.includes(
    transferMethod as B402TransferMethod,
  );
  const delegated = DELEGATED_WALLETS.has(walletKind);

  const supportedDelegated = catalogMatches && methodSupported && delegated;
  const supportedLocalEip3009 =
    catalogMatches &&
    LOCAL_WALLETS.has(walletKind) &&
    transferMethod === "eip3009" &&
    expectedAsset.eip3009Domain !== null &&
    knownPaymentTokens().has(
      `${expectedAsset.chainId}:${expectedAsset.address}`,
    );

  if (!supportedDelegated && !supportedLocalEip3009) {
    throw new UnsupportedWalletRouteError({
      walletKind,
      network: expectedAsset.network,
      chainId: expectedAsset.chainId,
      assetId: expectedAsset.assetId,
      transferMethod,
    });
  }

  return Object.freeze({
    walletKind,
    expectedAsset,
    transferMethod: transferMethod as B402TransferMethod,
    delegated,
  });
}

/**
 * `ERC8183Config` — ERC-8183 agent configuration (v1).
 *
 * Bundles the wallet + network plumbing every agent needs (mirroring
 * Python's `AgentConfig` base) with the three ERC-8183-specific concerns:
 *
 * - `storage`      — off-chain deliverable store.
 * - `servicePrice` — minimum budget (in raw token units) this provider
 *   will accept; used by `ERC8183JobOps.verifyJob` (`budget_too_low`) and
 *   by the `NegotiationHandler` to advertise a floor in quotes.
 * - `agentUrl`     — public base URL of this agent (required when storage
 *   returns a `file://` scheme).
 *
 * Contract-address overrides are NOT fields on this class. Use either:
 *
 * - Env vars `ERC8183_COMMERCE_ADDRESS` / `ERC8183_ROUTER_ADDRESS` /
 *   `ERC8183_POLICY_ADDRESS` (applied in {@link ERC8183Config.effectiveNetwork}).
 * - A pre-built `NetworkConfig` passed as `network` (fully explicit — env
 *   overrides are ignored in this mode).
 *
 * Wallet convenience: `privateKey` + `walletPassword` auto-wrap into an
 * `EVMWalletProvider`; both are cleared (kept at `""`) after construction
 * regardless of what was passed in — no plaintext secret material survives
 * as an instance field (INVARIANT).
 *
 * Env var surface (module-scoped, `ERC8183_` prefix)
 * ---------------------------------------------------
 *   ERC8183_COMMERCE_ADDRESS — override commerce_contract
 *   ERC8183_ROUTER_ADDRESS   — override router_contract
 *   ERC8183_POLICY_ADDRESS   — override policy_contract
 *   ERC8183_SERVICE_PRICE    — minimum budget floor (raw token units)
 *   ERC8183_AGENT_URL        — public base URL of this agent, e.g.
 *                              "http://host:8003/erc8183" (required when
 *                              storage returns file:// scheme)
 *
 * Global env vars: NETWORK / PRIVATE_KEY / WALLET_PASSWORD / WALLET_ADDRESS
 * / WALLET_KIND. With `WALLET_KIND=turnkey` the wallet is built from the
 * `TURNKEY_*` env vars (`TURNKEY_API_PUBLIC_KEY`, `TURNKEY_API_PRIVATE_KEY`,
 * `TURNKEY_ORG_ID`, `TURNKEY_SIGN_WITH`, optional `TURNKEY_API_BASE_URL`)
 * and WALLET_PASSWORD is not required.
 *
 * Payment token address is NOT configurable — it is immutable on the
 * Commerce kernel and fetched at runtime via `ERC8183Client.paymentToken`.
 *
 * Port of `python/bnbagent/erc8183/config.py` (+ the wallet/network fields
 * of `python/bnbagent/core/config.py::AgentConfig`, which has no standalone
 * TypeScript port yet).
 */

import { getAddress } from "viem";
import type { NetworkConfig } from "../config.js";
import { getEnv } from "../core/envUtil.js";
import { LocalStorageProvider } from "../storage/localStorageProvider.js";
import type { StorageProvider } from "../storage/storageProvider.js";
import { WalletIdentityMismatch } from "../wallets/errors.js";
import { EVMWalletProvider } from "../wallets/evmWalletProvider.js";
import { TurnkeyWalletProvider } from "../wallets/turnkey/provider.js";
import {
  TWAKProvider,
  TWAK_CHAIN_FOR_NETWORK,
} from "../wallets/twak/provider.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { ERC8183_ENV_PREFIX, resolveErc8183Network } from "./constants.js";

/** Constructor options for {@link ERC8183Config}. */
export interface ERC8183ConfigOpts {
  /** Preset name (`"bsc-testnet"`) or a concrete `NetworkConfig`. Defaults
   * to `"bsc-testnet"`. */
  network?: string | NetworkConfig;
  /** `WalletProvider` that owns all signing. Preferred when the caller
   * already has a provider; raw keys never need to be handled directly. */
  walletProvider?: WalletProvider | null;
  /** Auto-wrapped into `EVMWalletProvider` (requires `walletPassword`).
   * Cleared after construction — never survives as a field value. */
  privateKey?: string;
  /** Password for the `EVMWalletProvider` keystore. Cleared after
   * construction. */
  walletPassword?: string;
  /** Select a specific wallet from the keystore directory (by address). */
  walletAddress?: string;
  /** Select the wallet backend: `"evm"` / `""` (keystore), `"twak"`
   * (self-broadcasting CLI) or `"turnkey"` (remote enclave signer, built
   * from the `TURNKEY_*` env vars) — anything else throws. Advisory
   * metadata only when `walletProvider` is supplied directly. */
  walletKind?: string;
  /** Off-chain storage for deliverables. */
  storage?: StorageProvider | null;
  /** Minimum budget floor, raw token units (stringified int). */
  servicePrice?: string;
  /** Public base URL of this agent. */
  agentUrl?: string | null;
  /** Test-only override for the EVM keystore directory (defaults to
   * `~/.bnbagent/wallets`); not part of the Python surface. */
  walletsDir?: string;
}

/**
 * Configuration for an ERC-8183 agent (typically a provider).
 */
export class ERC8183Config {
  readonly network: string | NetworkConfig;
  readonly walletProvider: WalletProvider | null;
  /** Always `""` after construction (INVARIANT: no plaintext secrets
   * survive construction — the raw value is only ever held in a local
   * variable while wrapping into `walletProvider`). */
  readonly privateKey: string = "";
  /** Always `""` after construction, same invariant as {@link privateKey}. */
  readonly walletPassword: string = "";
  readonly walletAddress: string;
  readonly walletKind: string;
  readonly storage: StorageProvider | null;
  readonly servicePrice: string;
  readonly agentUrl: string | null;

  constructor(opts: ERC8183ConfigOpts = {}) {
    this.network = opts.network ?? "bsc-testnet";
    this.walletAddress = opts.walletAddress ?? "";
    this.walletKind = opts.walletKind ?? "";
    this.storage = opts.storage ?? null;
    this.servicePrice = opts.servicePrice ?? "1000000000000000000";
    this.agentUrl = opts.agentUrl ?? null;

    let privateKey = opts.privateKey ?? "";
    if (privateKey && !privateKey.startsWith("0x")) {
      privateKey = `0x${privateKey}`;
    }
    let walletPassword = opts.walletPassword ?? "";
    let walletProvider = opts.walletProvider ?? null;

    // Explicit wallet kind. "twak" dispatches to the self-broadcasting
    // TWAKProvider (chain auto-pinned to the network — Python parity);
    // any other non-EVM value is rejected outright, unless the caller
    // already supplied a wallet_provider (in which case wallet_kind is
    // advisory metadata only, mirroring AgentConfig).
    const kind = this.walletKind.trim().toLowerCase();
    if (kind === "twak" && !walletProvider) {
      const chain =
        typeof this.network === "string"
          ? TWAK_CHAIN_FOR_NETWORK[this.network]
          : undefined;
      if (!chain) {
        throw new Error(
          `WALLET_KIND=twak supports BNB Smart Chain networks only (${Object.keys(TWAK_CHAIN_FOR_NETWORK).join(", ")}); got network=${String(this.network)}`,
        );
      }
      walletProvider = new TWAKProvider({
        chain,
        ...(this.walletAddress ? { expectedAddress: this.walletAddress } : {}),
      });
    } else if (kind === "turnkey" && !walletProvider) {
      // Remote enclave signer, credentials from the TURNKEY_* env vars.
      // Pin the network's chain id so a mismatching transaction fails
      // closed before Turnkey's billable API call.
      walletProvider = TurnkeyWalletProvider.fromEnv({
        expectedChainId: this.effectiveChainId,
      });
      if (
        this.walletAddress &&
        getAddress(this.walletAddress) !== walletProvider.address
      ) {
        throw new WalletIdentityMismatch({
          expected: getAddress(this.walletAddress),
          actual: walletProvider.address,
        });
      }
    } else if (kind && kind !== "evm" && !walletProvider) {
      throw new Error(`Unknown wallet kind: ${this.walletKind}`);
    }

    if (privateKey && !walletProvider) {
      if (!walletPassword) {
        throw new Error(
          "wallet_password is required when using private_key. Pass wallet_provider= directly or set wallet_password.",
        );
      }
      walletProvider = new EVMWalletProvider({
        password: walletPassword,
        privateKey,
        walletsDir: opts.walletsDir,
      });
      privateKey = "";
      walletPassword = "";

      if (getEnv("PRIVATE_KEY")) {
        console.warn(
          "PRIVATE_KEY is still set in the environment after the keystore import succeeded. " +
            "The encrypted keystore at ~/.bnbagent/wallets/<address>.json is now the source of " +
            "truth — remove PRIVATE_KEY from your .env / shell to avoid leaking the plaintext key " +
            "via commits, backups, or container images.",
        );
      }
    } else if (!privateKey && !walletProvider && walletPassword) {
      // Password-only path: load an existing keystore if one is on disk,
      // otherwise let EVMWalletProvider auto-generate a fresh wallet.
      walletProvider = new EVMWalletProvider({
        password: walletPassword,
        address: this.walletAddress || undefined,
        walletsDir: opts.walletsDir,
      });
      walletPassword = "";
    }

    this.walletProvider = walletProvider;
  }

  toString(): string {
    const nc = this.effectiveNetwork;
    return (
      `ERC8183Config(network='${nc.name}', ${this.walletInfoRepr()}, ` +
      `commerce='${nc.commerceContract.slice(0, 10)}...', service_price=${this.servicePrice})`
    );
  }

  private walletInfoRepr(): string {
    if (!this.walletProvider) {
      return "wallet=None";
    }
    try {
      return `wallet='${this.walletProvider.address.slice(0, 10)}...'`;
    } catch {
      return "wallet='<configured>'";
    }
  }

  // ----------------------------------------------------------- effectives

  /**
   * Resolve `network` and overlay ERC-8183-scoped env overrides.
   *
   * Overlay precedence (highest → lowest):
   *   1. `ERC8183_COMMERCE_ADDRESS` / `ERC8183_ROUTER_ADDRESS` /
   *      `ERC8183_POLICY_ADDRESS` env vars.
   *   2. `RPC_URL` env var (applied during preset resolution).
   *   3. Preset defaults from `NETWORKS`.
   *
   * When `network` is already a `NetworkConfig` object, the caller takes
   * full control — env overrides are not applied.
   */
  get effectiveNetwork(): NetworkConfig {
    return resolveErc8183Network(this.network);
  }

  get effectiveRpcUrl(): string {
    return this.effectiveNetwork.rpcUrl;
  }

  get effectiveChainId(): number {
    return this.effectiveNetwork.chainId;
  }

  get effectiveCommerceAddress(): string {
    return this.effectiveNetwork.commerceContract;
  }

  get effectiveRouterAddress(): string {
    return this.effectiveNetwork.routerContract;
  }

  get effectivePolicyAddress(): string {
    return this.effectiveNetwork.policyContract;
  }

  // ---------------------------------------------------------------- loaders

  /**
   * Load ERC-8183 configuration from the environment.
   *
   * Global env vars (`NETWORK`, wallet keys) are read here directly (there
   * is no standalone `AgentConfig.fromEnv` in the TypeScript SDK yet).
   * ERC-8183-specific fields use the `ERC8183_` prefix and are resolved
   * lazily by {@link effectiveNetwork} so the env is always the single
   * source of truth.
   *
   * @param storage Optional pre-built `StorageProvider` instance. When
   *   omitted, `LocalStorageProvider.fromEnv()` is used.
   */
  static fromEnv(storage?: StorageProvider): ERC8183Config {
    const privateKey = getEnv("PRIVATE_KEY") ?? "";
    const walletPassword = getEnv("WALLET_PASSWORD") ?? "";
    const walletAddress = getEnv("WALLET_ADDRESS") ?? "";
    const walletKind = getEnv("WALLET_KIND") ?? "";

    // The WALLET_PASSWORD requirement is an EVM-kind concern: a non-EVM
    // kind (twak, turnkey) owns its custody end-to-end, so no password is
    // needed here — the constructor dispatches to the matching provider.
    const kind = walletKind.trim().toLowerCase();
    if (kind === "" || kind === "evm") {
      if (!walletPassword) {
        throw new Error(
          "ERC8183Config validation failed: WALLET_PASSWORD is required. " +
            "Set WALLET_PASSWORD to encrypt/decrypt the wallet keystore.",
        );
      }
    }

    const resolvedStorage = storage ?? LocalStorageProvider.fromEnv();

    return new ERC8183Config({
      network: getEnv("NETWORK", "bsc-testnet"),
      privateKey,
      walletPassword,
      walletAddress,
      walletKind,
      storage: resolvedStorage,
      servicePrice: getEnv(
        "SERVICE_PRICE",
        "1000000000000000000",
        ERC8183_ENV_PREFIX,
      ),
      agentUrl: getEnv("AGENT_URL", undefined, ERC8183_ENV_PREFIX) ?? null,
    });
  }

  static fromEnvOptional(): ERC8183Config | null {
    try {
      return ERC8183Config.fromEnv();
    } catch (error) {
      console.info(
        `[ERC8183Config] ERC-8183 not configured: ${error instanceof Error ? error.message : String(error)}`,
      );
      return null;
    }
  }
}

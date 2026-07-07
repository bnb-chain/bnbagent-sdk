/**
 * EVM Wallet Provider — Keystore V3 encryption.
 *
 * Manages EVM wallets with Keystore V3 encryption. Keystores are stored in
 * `~/.bnbagent/wallets/<checksum-address>.json`.
 *
 * Security:
 * - scrypt KDF + AES-128-CTR encryption (Keystore V3 / MetaMask / Geth
 *   compatible — see `./keystore.js`)
 * - File permissions 0o600 (owner read/write only)
 * - Directory permissions 0o700 (owner only)
 * - Private key only needed on first import; subsequent runs use password
 *   only
 *
 * Port of `python/bnbagent/wallets/evm_wallet_provider.py`.
 */

import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { bytesToHex, hexToBytes } from "@noble/hashes/utils";
import {
  type Hex,
  type TransactionRequestLegacy,
  type TransactionSerializableLegacy,
  type TypedDataDomain,
  hashMessage,
  hashTypedData,
  keccak256,
  parseSignature,
  parseTransaction,
} from "viem";
import {
  type PrivateKeyAccount,
  generatePrivateKey,
  privateKeyToAccount,
} from "viem/accounts";
import { check, inferPrimaryType } from "../signing/checks.js";
import { SigningPolicy } from "../signing/policy.js";
import { CALLS_ARBITRARY, PAYMASTER_SPONSOR } from "./capabilities.js";
import { decryptKeystoreV3, encryptKeystoreV3 } from "./keystore.js";
import type { KeystoreV3 } from "./keystore.js";
import type { SignatureResult, SignedTx } from "./walletProvider.js";
import { WalletProvider } from "./walletProvider.js";

const DEFAULT_WALLETS_DIR = join(homedir(), ".bnbagent", "wallets");

/** How the wallet was initialized. */
export type WalletSource = "" | "imported" | "loaded_keystore" | "created_new";

/** Constructor options for {@link EVMWalletProvider}. */
export interface EVMWalletProviderOptions {
  /** Password for Keystore encryption/decryption (REQUIRED). */
  password: string;
  /**
   * Private key to import (hex, with or without `0x`). Only needed on first
   * run; encrypted to disk afterward.
   */
  privateKey?: string;
  /**
   * Address of an existing keystore to load. If omitted and no
   * `privateKey` is given, auto-selects if exactly one keystore exists.
   */
  address?: string;
  /** Save encrypted keystore to disk (default: `true`). */
  persist?: boolean;
  /** Override wallet directory (default: `~/.bnbagent/wallets/`). */
  walletsDir?: string;
  /**
   * Policy applied to every {@link EVMWalletProvider.signTypedData} call.
   * Defaults to {@link SigningPolicy.strictDefault}.
   */
  signingPolicy?: SigningPolicy;
}

function listKeystoreFiles(dir: string): string[] {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return [];
  return readdirSync(dir).filter(
    (f) => f.startsWith("0x") && f.endsWith(".json"),
  );
}

/**
 * EVM wallet provider with Keystore V3 encryption.
 *
 * Wallets are stored as individual JSON files in `~/.bnbagent/wallets/`,
 * named by address (e.g. `0x1234...abcd.json`).
 *
 * Typical lifecycle:
 * ```ts
 * // First run — import and encrypt
 * const wallet = new EVMWalletProvider({ password: "pw", privateKey: "0x..." });
 *
 * // Subsequent runs — load from keystore (no private key needed)
 * const wallet2 = new EVMWalletProvider({ password: "pw", address: "0x1234...abcd" });
 *
 * // Auto-select — if only one wallet exists
 * const wallet3 = new EVMWalletProvider({ password: "pw" });
 * ```
 */
export class EVMWalletProvider extends WalletProvider {
  static override readonly kind = "evm";

  // Arbitrary mechanical contract calls via LocalExecutor; sponsored
  // broadcast via the MegaFuel paymaster. sign.* derive automatically since
  // all three sign methods below are overridden.
  protected override readonly extraCapabilities: ReadonlySet<string> = new Set([
    CALLS_ARBITRARY,
    PAYMASTER_SPONSOR,
  ]);

  readonly #password: string;
  readonly #persist: boolean;
  readonly #walletsDir: string;
  readonly #signingPolicy: SigningPolicy;

  #account: PrivateKeyAccount | undefined;
  // Raw private key hex, kept alongside the viem account since
  // `PrivateKeyAccount` does not expose it (needed for keystore
  // export/re-encryption and exportPrivateKey()).
  #privateKeyHex: Hex | undefined;
  #source: WalletSource = "";

  /**
   * @throws {Error} If `password` is empty, `privateKey` is invalid, or no
   *   wallet can be resolved.
   */
  constructor(opts: EVMWalletProviderOptions) {
    super();
    if (!opts.password) {
      throw new Error(
        "Password is required for wallet encryption. Please provide a secure password.",
      );
    }

    this.#password = opts.password;
    this.#persist = opts.persist ?? true;
    this.#walletsDir = opts.walletsDir ?? DEFAULT_WALLETS_DIR;
    this.#signingPolicy = opts.signingPolicy ?? SigningPolicy.strictDefault();

    if (opts.privateKey) {
      this.#importPrivateKey(opts.privateKey);
    } else if (this.#persist) {
      this.#loadWallet(opts.address);
    } else {
      throw new Error(
        "private_key is required when persist=false (in-memory-only mode)",
      );
    }
  }

  // ── Static helpers ──────────────────────────────────────────────────

  /**
   * Check if an encrypted keystore exists on disk.
   *
   * @param address Check for a specific address (checksummed, `0x`-prefixed).
   *   If omitted, returns `true` if *any* keystore file exists.
   * @param walletsDir Override wallet directory.
   */
  static keystoreExists(address?: string, walletsDir?: string): boolean {
    const dir = walletsDir ?? DEFAULT_WALLETS_DIR;
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return false;
    if (address) return existsSync(join(dir, `${address}.json`));
    return listKeystoreFiles(dir).length > 0;
  }

  /**
   * List all wallet addresses that have keystores on disk.
   *
   * @returns Checksummed addresses (e.g. `["0x1234...abcd"]`), sorted.
   */
  static listWallets(walletsDir?: string): string[] {
    const dir = walletsDir ?? DEFAULT_WALLETS_DIR;
    return listKeystoreFiles(dir)
      .map((f) => f.slice(0, -".json".length))
      .sort();
  }

  /** How the wallet was initialized: `"imported"`, `"loaded_keystore"`, or `"created_new"`. */
  get source(): WalletSource {
    return this.#source;
  }

  /** The SigningPolicy currently enforcing {@link signTypedData} calls. */
  get signingPolicy(): SigningPolicy {
    return this.#signingPolicy;
  }

  // ── Private key import ──────────────────────────────────────────────

  #importPrivateKey(privateKey: string): void {
    try {
      const stripped = privateKey.startsWith("0x")
        ? privateKey.slice(2)
        : privateKey;
      if (stripped.length !== 64) {
        throw new Error("Private key must be 64 hex characters (32 bytes)");
      }
      const pkHex = `0x${stripped}` as Hex;
      const account = privateKeyToAccount(pkHex);
      this.#privateKeyHex = pkHex;
      this.#account = account;
      this.#source = "imported";

      if (this.#persist) {
        this.#saveKeystore();
      }
    } catch (e) {
      throw new Error(`Invalid private key: ${(e as Error).message}`);
    }
  }

  // ── Load from disk ───────────────────────────────────────────────────

  #loadWallet(address: string | undefined): void {
    if (address) {
      this.#loadKeystore(address);
      return;
    }
    const wallets = EVMWalletProvider.listWallets(this.#walletsDir);
    if (wallets.length === 1) {
      this.#loadKeystore(wallets[0] as string);
    } else if (wallets.length > 1) {
      const listRepr = `[${wallets.map((w) => `'${w}'`).join(", ")}]`;
      throw new Error(
        `Multiple wallets found in ${this.#walletsDir}: ${listRepr}. Set WALLET_ADDRESS to specify which one to use.`,
      );
    } else {
      this.#createWallet();
    }
  }

  #loadKeystore(address: string): void {
    const ksPath = join(this.#walletsDir, `${address}.json`);
    if (!existsSync(ksPath)) {
      throw new Error(`Keystore not found: ${ksPath}`);
    }
    try {
      const raw = readFileSync(ksPath, "utf8");
      const keystore = JSON.parse(raw) as KeystoreV3;
      const privateKeyBytes = decryptKeystoreV3(keystore, this.#password);
      const pkHex = `0x${bytesToHex(privateKeyBytes)}` as Hex;
      this.#privateKeyHex = pkHex;
      this.#account = privateKeyToAccount(pkHex);
      this.#source = "loaded_keystore";
    } catch (e) {
      const err = e as Error;
      if (err.message.startsWith("Failed to decrypt keystore")) {
        throw err;
      }
      throw new Error(`Failed to load keystore ${ksPath}: ${err.message}`);
    }
  }

  #createWallet(): void {
    const pkHex = generatePrivateKey();
    this.#privateKeyHex = pkHex;
    this.#account = privateKeyToAccount(pkHex);
    this.#source = "created_new";
    if (this.#persist) {
      this.#saveKeystore();
    }
  }

  // ── Save to disk ─────────────────────────────────────────────────────

  #saveKeystore(): void {
    mkdirSync(this.#walletsDir, { recursive: true });
    chmodSync(this.#walletsDir, 0o700);

    const privateKeyBytes = hexToBytes(this.#requirePrivateKeyHex().slice(2));
    const keystore = encryptKeystoreV3(privateKeyBytes, this.#password);
    const ksPath = join(
      this.#walletsDir,
      `${this.#requireAccount().address}.json`,
    );

    const tmpPath = join(
      this.#walletsDir,
      `.ks_${Date.now()}_${Math.random().toString(36).slice(2)}.tmp`,
    );
    try {
      // Create the temp file restricted from the outset (mode is subject to
      // umask, so chmod afterwards to make it authoritative regardless of
      // umask) — the encrypted keystore must never be world/group-readable,
      // even for the brief window before rename.
      writeFileSync(tmpPath, JSON.stringify(keystore), { mode: 0o600 });
      chmodSync(tmpPath, 0o600);
      renameSync(tmpPath, ksPath);
    } catch (e) {
      try {
        if (existsSync(tmpPath)) {
          // Clean up the orphaned temp file so stale `.ks_*.tmp` encrypted
          // keys don't accumulate on disk; never let a cleanup failure mask
          // the original error.
          unlinkSync(tmpPath);
        }
      } catch {
        // ignore secondary failure; original error takes priority
      }
      throw e;
    }
  }

  // ── Public API ───────────────────────────────────────────────────────

  #requireAccount(): PrivateKeyAccount {
    if (!this.#account) throw new Error("Account not initialized");
    return this.#account;
  }

  #requirePrivateKeyHex(): Hex {
    if (!this.#privateKeyHex) throw new Error("Account not initialized");
    return this.#privateKeyHex;
  }

  get address(): `0x${string}` {
    return this.#requireAccount().address;
  }

  /** Sign a legacy (gasPrice) transaction. */
  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    const account = this.#requireAccount();
    // `TransactionRequestLegacy` (the base class's declared parameter type)
    // omits `chainId` — callers pass it anyway for EIP-155 signing, mirroring
    // `TransactionSerializableLegacy` (viem's actual signing input type).
    const transaction = tx as unknown as TransactionSerializableLegacy;
    const rawTransaction = await account.signTransaction(transaction);
    const parsed = parseTransaction(rawTransaction) as {
      r: `0x${string}`;
      s: `0x${string}`;
      v: bigint;
    };
    const hash = keccak256(rawTransaction);
    return {
      rawTransaction,
      hash,
      r: parsed.r,
      s: parsed.s,
      v: parsed.v,
    };
  }

  /** Sign a message using EIP-191 personal sign. */
  override async signMessage(message: string): Promise<SignatureResult> {
    const account = this.#requireAccount();
    const signature = await account.signMessage({ message });
    const { r, s, v } = parseSignature(signature);
    return {
      messageHash: hashMessage(message),
      r,
      s,
      v: v as bigint,
      signature,
    };
  }

  /**
   * Sign EIP-712 typed data after passing the configured {@link SigningPolicy}.
   *
   * The policy check runs first and may throw
   * {@link import("../signing/errors.js").PolicyViolation}.
   */
  override async signTypedData(
    domain: TypedDataDomain,
    types: Record<string, { name: string; type: string }[]>,
    message: Record<string, unknown>,
  ): Promise<SignatureResult> {
    check(
      this.#signingPolicy,
      domain as Record<string, unknown>,
      types,
      message,
    );
    return this.#rawSignTypedData(domain, types, message);
  }

  /**
   * BYPASSES SigningPolicy. Tests + trusted SDK code only.
   *
   * Calls the raw EIP-712 signer without policy enforcement. Every call
   * emits a `console.warn` so audit grep can find bypasses. Production /
   * agent-reachable code MUST NOT call this.
   */
  async _DANGEROUS_signTypedDataNoPolicy(
    domain: TypedDataDomain,
    types: Record<string, { name: string; type: string }[]>,
    message: Record<string, unknown>,
  ): Promise<SignatureResult> {
    const caller = EVMWalletProvider.#callerLocation();
    console.warn(
      `_DANGEROUS_sign_typed_data_no_policy invoked from ${caller} — POLICY BYPASS`,
    );
    return this.#rawSignTypedData(domain, types, message);
  }

  static #callerLocation(): string {
    const stack = new Error().stack ?? "";
    const lines = stack.split("\n").slice(1);
    // Skip frames inside this module (constructor + this static helper).
    const frame = lines.find((l) => !l.includes("evmWalletProvider"));
    return (frame ?? lines[0] ?? "<unknown>").trim();
  }

  async #rawSignTypedData(
    domain: TypedDataDomain,
    types: Record<string, { name: string; type: string }[]>,
    message: Record<string, unknown>,
  ): Promise<SignatureResult> {
    const account = this.#requireAccount();
    // viem expects `types` without the EIP712Domain entry — it derives its
    // own from `domain`. Drop it if the caller included it (a common
    // convention); this also guarantees identical signatures whether or not
    // the caller supplied EIP712Domain.
    const messageTypes = Object.fromEntries(
      Object.entries(types).filter(([k]) => k !== "EIP712Domain"),
    );
    const primaryType = inferPrimaryType(types);
    const signature = await account.signTypedData({
      domain,
      types: messageTypes,
      primaryType,
      message,
    } as Parameters<typeof account.signTypedData>[0]);
    const { r, s, v } = parseSignature(signature);
    const messageHash = hashTypedData({
      domain,
      types: messageTypes,
      primaryType,
      message,
    } as Parameters<typeof hashTypedData>[0]);
    return { messageHash, r, s, v: v as bigint, signature };
  }

  /** Export the private key in hex format. Handle with extreme care. */
  exportPrivateKey(): Hex {
    return this.#requirePrivateKeyHex();
  }

  /** Export the wallet as Keystore V3 JSON (MetaMask/Geth compatible). */
  exportKeystore(): KeystoreV3 {
    const privateKeyBytes = hexToBytes(this.#requirePrivateKeyHex().slice(2));
    return encryptKeystoreV3(privateKeyBytes, this.#password);
  }

  /** Path of the encrypted Keystore V3 file, or an in-memory marker. */
  override get keyLocation(): string | null {
    if (!this.#persist) return "in-memory (not persisted)";
    if (!this.#account) return this.#walletsDir;
    return join(this.#walletsDir, `${this.#account.address}.json`);
  }

  /**
   * `true` if an encrypted keystore for this address is on disk.
   *
   * In-memory-only wallets (`persist=false`) have no durable key material
   * and always report `false`.
   */
  override exists(): boolean {
    if (!this.#persist || !this.#account) return false;
    return EVMWalletProvider.keystoreExists(
      this.#account.address,
      this.#walletsDir,
    );
  }
}

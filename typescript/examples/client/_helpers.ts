/**
 * Shared helpers for the ERC-8183 client flow demos.
 *
 * Port of `python/examples/client/_helpers.py`. The TypeScript SDK only
 * ships an EVM wallet provider (no twak/CLI-delegated signer), so unlike the
 * Python reference there is no `WALLET_KIND` switch here — every flow uses
 * an ephemeral `EVMWalletProvider` built straight from a raw testnet
 * private key.
 */

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { ERC8183Client, EVMWalletProvider, loadEnv } from "../../src/index.js";

/** Directory this file lives in — `.env`/`.env.local` are loaded from here,
 * mirroring the Python reference's `ROOT = Path(__file__).resolve().parent`. */
export const ROOT: string = dirname(fileURLToPath(import.meta.url));

/** Load `.env.local` then `.env` from {@link ROOT} (never overriding real
 * environment variables — see {@link loadEnv}'s docstring for precedence). */
export function loadEnvFile(): void {
  loadEnv(ROOT);
}

function requireEnv(name: string): string {
  const val = process.env[name];
  if (!val) {
    throw new Error(`${name} is required in .env`);
  }
  return val;
}

/** Settings shared by every client demo script. */
export interface Settings {
  network: string;
  clientPk: string;
  providerAddress: string;
  providerPk: string | null;
  voterPk: string | null;
}

/** Load `.env`/`.env.local` and read the demo's required/optional env vars. */
export function loadSettings(): Settings {
  loadEnvFile();
  return {
    network: process.env.NETWORK ?? "bsc-testnet",
    clientPk: requireEnv("PRIVATE_KEY"),
    providerAddress: requireEnv("PROVIDER_ADDRESS"),
    providerPk: process.env.PROVIDER_PRIVATE_KEY || null,
    voterPk: process.env.VOTER_PRIVATE_KEY || null,
  };
}

/**
 * Wrap a raw testnet private key into an ephemeral wallet provider.
 *
 * `persist: false` keeps the demo hermetic — no keystore files are written
 * to `~/.bnbagent/wallets`. Do NOT reuse this pattern for production keys.
 */
export function makeWallet(pk: string): EVMWalletProvider {
  return new EVMWalletProvider({
    password: "example",
    privateKey: pk,
    persist: false,
  });
}

/** Build an `ERC8183Client` for an arbitrary role (client/provider/voter) from a raw PK. */
export function makeClient(
  pk: string,
  network = "bsc-testnet",
): Promise<ERC8183Client> {
  return ERC8183Client.create({ walletProvider: makeWallet(pk), network });
}

/** Build the CLIENT-role `ERC8183Client` from {@link Settings}. */
export function makePrimaryClient(s: Settings): Promise<ERC8183Client> {
  return makeClient(s.clientPk, s.network);
}

/**
 * Return an `expiredAt` that fits the policy's dispute window.
 *
 * The on-chain `OptimisticPolicy` rejects `commerce.submit` with
 * `SubmissionTooLate` unless `submitTime + disputeWindow <= expiredAt`, so
 * `expiredAt = now + disputeWindow + slack`. `slack` is the provider's
 * window to complete poll -> on_job -> IPFS upload -> on-chain submit before
 * the deadline expires.
 *
 * The 10-minute default fits a clean happy-path run. It is **demo-grade
 * only** — production clients should set `slackMinutes` to hours or days.
 */
export async function expiryFor(
  client: ERC8183Client,
  slackMinutes = 10,
): Promise<bigint> {
  const disputeWindow = await client.policy.disputeWindow();
  const now = BigInt(Math.floor(Date.now() / 1000));
  return now + disputeWindow + BigInt(slackMinutes * 60);
}

/** Print a boxed banner, mirroring the Python demos' `banner()`. */
export function banner(msg: string): void {
  console.log();
  console.log("=".repeat(60));
  console.log(` ${msg}`);
  console.log("=".repeat(60));
}

/** `await sleep(ms)` — used by the demos' wall-clock waits. */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

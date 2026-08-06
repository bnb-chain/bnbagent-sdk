/**
 * End-to-end security validation for SigningPolicy + X402Signer.
 *
 * Run this script after any change to the signing layer. It does NOT send
 * any transaction — purely off-chain sign / recover round-trips. The 6
 * assertions exercise the canonical defense matrix on BSC testnet's real
 * U-token EIP-712 domain.
 *
 * Usage:
 *     pnpm -C typescript run example:security
 *
 * Exit code 0 + 6 assertions logged means the policy stack is intact. Fully
 * offline — CI-runnable.
 *
 * Port of `python/examples/security/e2e.py`.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  type TypedDataDomain,
  recoverTypedDataAddress,
  getAddress as toChecksumAddress,
} from "viem";
import { generatePrivateKey } from "viem/accounts";
import {
  BSC_TESTNET_CHAIN_ID,
  PAYMENT_TOKEN_EIP712_NAME,
  PAYMENT_TOKEN_EIP712_VERSION,
  getAddress,
} from "../../src/networks/index.js";
import { PolicyViolation, SigningPolicy } from "../../src/signing/index.js";
import { EVMWalletProvider } from "../../src/wallets/index.js";
import {
  X402AmountExceededError,
  X402RecipientMismatchError,
  X402Signer,
} from "../../src/x402/index.js";

// ── Fixtures ─────────────────────────────────────────────────────────────

const PW = "e2e-secure-pw";
// Ephemeral key — this script never broadcasts. Override via E2E_PRIVATE_KEY
// if you need a deterministic key for a specific repro.
const PK = process.env.E2E_PRIVATE_KEY || generatePrivateKey();
const U_TESTNET = getAddress(BSC_TESTNET_CHAIN_ID).paymentToken;

const EIP712_DOMAIN_FIELDS = [
  { name: "name", type: "string" },
  { name: "version", type: "string" },
  { name: "chainId", type: "uint256" },
  { name: "verifyingContract", type: "address" },
];
const TWA_FIELDS = [
  { name: "from", type: "address" },
  { name: "to", type: "address" },
  { name: "value", type: "uint256" },
  { name: "validAfter", type: "uint256" },
  { name: "validBefore", type: "uint256" },
  { name: "nonce", type: "bytes32" },
];
const PERMIT_FIELDS = [
  { name: "owner", type: "address" },
  { name: "spender", type: "address" },
  { name: "value", type: "uint256" },
  { name: "nonce", type: "uint256" },
  { name: "deadline", type: "uint256" },
];

function banner(msg: string): void {
  console.log("=".repeat(60));
  console.log(msg);
}

function makeWallet(
  walletsDir: string,
  opts: { signingPolicy?: SigningPolicy } = {},
): EVMWalletProvider {
  return new EVMWalletProvider({
    password: PW,
    privateKey: PK,
    walletsDir,
    signingPolicy: opts.signingPolicy,
  });
}

function twaMessage(
  wallet: EVMWalletProvider,
  opts: { to?: `0x${string}`; value?: bigint } = {},
): Record<string, unknown> {
  const now = Math.floor(Date.now() / 1000);
  return {
    from: wallet.address,
    to: opts.to ?? `0x${"b".repeat(40)}`,
    value: opts.value ?? 100_000n,
    validAfter: now - 60,
    validBefore: now + 60,
    nonce: `0x${"c".repeat(64)}`,
  };
}

function twaDomain(): TypedDataDomain {
  return {
    name: PAYMENT_TOKEN_EIP712_NAME,
    version: PAYMENT_TOKEN_EIP712_VERSION,
    chainId: BSC_TESTNET_CHAIN_ID,
    verifyingContract: U_TESTNET,
  };
}

// ── Assertions ─────────────────────────────────────────────────────────────

async function assertion1DefaultSignsUTokenAndRoundTrips(
  walletsDir: string,
): Promise<void> {
  banner("Assertion 1: default wallet signs U-token TWA + recovers signer");
  const wallet = makeWallet(walletsDir);
  const domain = twaDomain();
  const types = {
    EIP712Domain: EIP712_DOMAIN_FIELDS,
    TransferWithAuthorization: TWA_FIELDS,
  };
  const msg = twaMessage(wallet);
  const signed = await wallet.signTypedData(domain, types, msg);

  const recovered = await recoverTypedDataAddress({
    domain,
    types: { TransferWithAuthorization: TWA_FIELDS },
    primaryType: "TransferWithAuthorization",
    message: msg,
    signature: signed.signature,
  });
  if (recovered.toLowerCase() !== wallet.address.toLowerCase()) {
    throw new Error(`recovered ${recovered} != wallet ${wallet.address}`);
  }
  console.log(
    `  -> signed by ${wallet.address}, recovered ${recovered} (match)`,
  );
}

async function assertion2DefaultRejectsUnknownVerifyingContract(
  walletsDir: string,
): Promise<void> {
  banner("Assertion 2: default wallet rejects unknown verifyingContract");
  const wallet = makeWallet(walletsDir);
  const domain: TypedDataDomain = {
    ...twaDomain(),
    verifyingContract: `0x${"1".repeat(40)}` as `0x${string}`,
  };
  const types = {
    EIP712Domain: EIP712_DOMAIN_FIELDS,
    TransferWithAuthorization: TWA_FIELDS,
  };
  try {
    await wallet.signTypedData(domain, types, twaMessage(wallet));
  } catch (error) {
    if (error instanceof PolicyViolation) {
      console.log(`  -> PolicyViolation as expected: ${error.message}`);
      if (error.primaryType !== "TransferWithAuthorization") {
        throw new Error(
          `expected primaryType TransferWithAuthorization, got ${error.primaryType}`,
        );
      }
      if (error.chainId !== BSC_TESTNET_CHAIN_ID) {
        throw new Error(
          `expected chainId ${BSC_TESTNET_CHAIN_ID}, got ${error.chainId}`,
        );
      }
      return;
    }
    throw error;
  }
  throw new Error("expected PolicyViolation");
}

async function assertion3DefaultRejectsEip2612Permit(
  walletsDir: string,
): Promise<void> {
  banner("Assertion 3: default wallet rejects EIP-2612 Permit (denylist)");
  const wallet = makeWallet(walletsDir);
  const domain = twaDomain(); // U-token's real domain (Permit is supported on chain)
  const types = { EIP712Domain: EIP712_DOMAIN_FIELDS, Permit: PERMIT_FIELDS };
  const msg = {
    owner: wallet.address,
    spender: `0x${"b".repeat(40)}`,
    value: 2n ** 256n - 1n,
    nonce: 0,
    deadline: 2_000_000_000,
  };
  try {
    await wallet.signTypedData(domain, types, msg);
  } catch (error) {
    if (error instanceof PolicyViolation) {
      console.log(
        `  -> PolicyViolation as expected (denylist): ${error.message}`,
      );
      if (error.primaryType !== "Permit") {
        throw new Error(
          `expected primaryType Permit, got ${error.primaryType}`,
        );
      }
      return;
    }
    throw error;
  }
  throw new Error("expected PolicyViolation for Permit");
}

async function assertion4ExtendedPolicyAcceptsCustomContract(
  walletsDir: string,
): Promise<void> {
  banner("Assertion 4: extended policy accepts a custom verifyingContract");
  const custom = toChecksumAddress(`0x${"9".repeat(40)}` as `0x${string}`);
  const extended = SigningPolicy.strictDefault().extend({
    domainAllowlist: [[BSC_TESTNET_CHAIN_ID, custom]],
  });
  const wallet = makeWallet(walletsDir, { signingPolicy: extended });
  const domain: TypedDataDomain = { ...twaDomain(), verifyingContract: custom };
  const types = {
    EIP712Domain: EIP712_DOMAIN_FIELDS,
    TransferWithAuthorization: TWA_FIELDS,
  };
  const signed = await wallet.signTypedData(domain, types, twaMessage(wallet));
  console.log(
    `  -> signed against custom ${custom} (sig=${signed.signature.slice(0, 18)})`,
  );
}

async function assertion5X402SignerRejectsOvervalue(
  walletsDir: string,
): Promise<void> {
  banner("Assertion 5: X402Signer rejects value over max_value_per_call");
  const wallet = makeWallet(walletsDir);
  const signer = new X402Signer(wallet, {
    maxValuePerCall: { [U_TESTNET]: 1_000_000n },
  });
  const domain = twaDomain();
  const types = {
    EIP712Domain: EIP712_DOMAIN_FIELDS,
    TransferWithAuthorization: TWA_FIELDS,
  };
  const msg = twaMessage(wallet, { value: 2_000_000n });
  try {
    await signer.signPayment({
      domain,
      types,
      message: msg,
      expectedTo: msg.to as string,
    });
  } catch (error) {
    if (error instanceof X402AmountExceededError) {
      console.log(`  -> X402AmountExceededError as expected: ${error.message}`);
      return;
    }
    throw error;
  }
  throw new Error("expected X402AmountExceededError");
}

async function assertion6X402SignerRejectsRecipientMismatch(
  walletsDir: string,
): Promise<void> {
  banner("Assertion 6: X402Signer rejects expectedTo mismatch");
  const wallet = makeWallet(walletsDir);
  const signer = new X402Signer(wallet, {
    maxValuePerCall: { [U_TESTNET]: 1_000_000n },
  });
  const domain = twaDomain();
  const types = {
    EIP712Domain: EIP712_DOMAIN_FIELDS,
    TransferWithAuthorization: TWA_FIELDS,
  };
  const msg = twaMessage(wallet, { to: `0x${"b".repeat(40)}` });
  try {
    await signer.signPayment({
      domain,
      types,
      message: msg,
      expectedTo: `0x${"9".repeat(40)}`, // different!
    });
  } catch (error) {
    if (error instanceof X402RecipientMismatchError) {
      console.log(
        `  -> X402RecipientMismatchError as expected: ${error.message}`,
      );
      return;
    }
    throw error;
  }
  throw new Error("expected X402RecipientMismatchError");
}

// ── Main ──────────────────────────────────────────────────────────────────

async function main(): Promise<number> {
  console.log("SDK security_e2e — defense-in-depth signing validation");
  console.log(
    `Wallet PK: ${PK.slice(0, 6)}...${PK.slice(-4)} (in-memory only)`,
  );
  console.log(`Network: BSC testnet (chainId=${BSC_TESTNET_CHAIN_ID})`);

  const walletsDir = mkdtempSync(join(tmpdir(), "bnbagent-security-e2e-"));
  try {
    for (const fn of [
      assertion1DefaultSignsUTokenAndRoundTrips,
      assertion2DefaultRejectsUnknownVerifyingContract,
      assertion3DefaultRejectsEip2612Permit,
      assertion4ExtendedPolicyAcceptsCustomContract,
      assertion5X402SignerRejectsOvervalue,
      assertion6X402SignerRejectsRecipientMismatch,
    ]) {
      await fn(walletsDir);
    }
  } finally {
    rmSync(walletsDir, { recursive: true, force: true });
  }

  console.log("=".repeat(60));
  console.log("ALL 6 ASSERTIONS PASSED");
  return 0;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });

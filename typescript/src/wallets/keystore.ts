/**
 * Keystore V3 (Web3 Secret Storage) encryption/decryption.
 *
 * eth-account / Geth / MetaMask compatible: scrypt (or pbkdf2-hmac-sha256 on
 * read) KDF + AES-128-CTR cipher + a keccak256 MAC over
 * `dk[16:32] || ciphertext`. This is the on-disk format written by
 * `EVMWalletProvider` and MUST interoperate byte-for-byte with keystores
 * produced by Python's `eth_account.Account.encrypt` (and vice versa) — see
 * `tests/fixtures/keystore-interop.json` and `tests/wallet.test.ts`.
 *
 * Port of the encryption half of `python/bnbagent/wallets/evm_wallet_provider.py`
 * (which itself delegates to `eth_account.Account.encrypt` / `.decrypt`).
 */

import { ctr } from "@noble/ciphers/aes";
import { pbkdf2 } from "@noble/hashes/pbkdf2";
import { scrypt } from "@noble/hashes/scrypt";
import { sha256 } from "@noble/hashes/sha2";
import { bytesToHex, hexToBytes, randomBytes } from "@noble/hashes/utils";
import { keccak256 } from "viem";
import { privateKeyToAccount } from "viem/accounts";

/**
 * Encode a password as raw UTF-8 bytes.
 *
 * Deliberately NOT viem's `toBytes` — that helper auto-detects `0x`-prefixed
 * strings as hex and would silently mis-encode a password that happens to
 * look like hex (e.g. `"0xdeadbeef"`). Keystore passwords are always taken
 * literally as UTF-8 text, matching Python's `str.encode()` default used by
 * `eth_account`/`eth_keyfile`.
 */
function passwordBytes(password: string): Uint8Array {
  return new TextEncoder().encode(password);
}

const SCRYPT_N = 262_144;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const DK_LEN = 32;
const MAX_PBKDF2_ITERATIONS = 1_000_000;
const MIN_SALT_BYTES = 16;
const MAX_SALT_BYTES = 64;

/** Scrypt KDF parameters as stored in a Keystore V3 file. */
export interface ScryptKdfParams {
  dklen: number;
  n: number;
  r: number;
  p: number;
  salt: string;
}

/** PBKDF2-HMAC-SHA256 KDF parameters as stored in a Keystore V3 file. */
export interface Pbkdf2KdfParams {
  dklen: number;
  c: number;
  prf: string;
  salt: string;
}

/** Keystore V3 (Web3 Secret Storage) document. */
export interface KeystoreV3 {
  version: 3;
  id: string;
  address: string;
  crypto: {
    cipher: "aes-128-ctr";
    cipherparams: { iv: string };
    ciphertext: string;
    kdf: "scrypt" | "pbkdf2";
    kdfparams: ScryptKdfParams | Pbkdf2KdfParams;
    mac: string;
  };
}

function randomUuidV4(): string {
  const bytes = randomBytes(16);
  bytes[6] = ((bytes[6] as number) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80;
  const hex = bytesToHex(bytes);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function assertIntegerInRange(
  value: unknown,
  name: string,
  min: number,
  max: number,
): asserts value is number {
  if (!Number.isSafeInteger(value) || (value as number) < min) {
    throw new Error(`${name} must be an integer >= ${min}`);
  }
  if ((value as number) > max) {
    throw new Error(`${name} exceeds supported maximum ${max}`);
  }
}

function assertHexBytes(
  value: unknown,
  name: string,
  minBytes: number,
  maxBytes = minBytes,
): asserts value is string {
  if (
    typeof value !== "string" ||
    value.length % 2 !== 0 ||
    !/^[0-9a-fA-F]+$/.test(value)
  ) {
    throw new Error(`${name} must be hexadecimal`);
  }
  const bytes = value.length / 2;
  if (bytes < minBytes || bytes > maxBytes) {
    const expected =
      minBytes === maxBytes
        ? `${minBytes} bytes`
        : `${minBytes}-${maxBytes} bytes`;
    throw new Error(`${name} must be ${expected}`);
  }
}

/** Validate all attacker-controlled fields before invoking a costly KDF. */
function validateKeystoreForDecrypt(keystore: KeystoreV3): void {
  if (keystore?.version !== 3) {
    throw new Error(
      `unsupported keystore version: ${String(keystore?.version)}`,
    );
  }
  const c = keystore.crypto;
  if (!c || typeof c !== "object") {
    throw new Error("keystore crypto section is required");
  }
  if (c.cipher !== "aes-128-ctr") {
    throw new Error(`unsupported keystore cipher: ${String(c.cipher)}`);
  }
  assertHexBytes(c.cipherparams?.iv, "crypto.cipherparams.iv", 16);
  assertHexBytes(c.ciphertext, "crypto.ciphertext", 32);
  assertHexBytes(c.mac, "crypto.mac", 32);

  const params = c.kdfparams;
  if (!params || typeof params !== "object") {
    throw new Error("crypto.kdfparams is required");
  }
  assertIntegerInRange(params.dklen, "crypto.kdfparams.dklen", DK_LEN, DK_LEN);
  assertHexBytes(
    params.salt,
    "crypto.kdfparams.salt",
    MIN_SALT_BYTES,
    MAX_SALT_BYTES,
  );

  if (c.kdf === "scrypt") {
    const scryptParams = params as ScryptKdfParams & { N?: number };
    const n = scryptParams.n ?? scryptParams.N;
    assertIntegerInRange(n, "scrypt n", 2, SCRYPT_N);
    if ((n & (n - 1)) !== 0) {
      throw new Error("scrypt n must be a power of two");
    }
    assertIntegerInRange(scryptParams.r, "scrypt r", 1, SCRYPT_R);
    assertIntegerInRange(scryptParams.p, "scrypt p", 1, SCRYPT_P);
    return;
  }
  if (c.kdf === "pbkdf2") {
    const pbkdf2Params = params as Pbkdf2KdfParams;
    if (pbkdf2Params.prf !== "hmac-sha256") {
      throw new Error(`unsupported pbkdf2 prf: ${String(pbkdf2Params.prf)}`);
    }
    assertIntegerInRange(pbkdf2Params.c, "pbkdf2 c", 1, MAX_PBKDF2_ITERATIONS);
    return;
  }
  throw new Error(`unsupported kdf: ${String(c.kdf)}`);
}

function deriveKey(
  password: string,
  kdf: "scrypt" | "pbkdf2",
  kdfparams: ScryptKdfParams | Pbkdf2KdfParams,
): Uint8Array {
  const pwBytes = passwordBytes(password);
  const salt = hexToBytes(kdfparams.salt);
  if (kdf === "scrypt") {
    const p = kdfparams as ScryptKdfParams & { N?: number };
    const n = p.n ?? p.N;
    if (n === undefined) {
      throw new Error("scrypt kdfparams missing n/N");
    }
    return scrypt(pwBytes, salt, {
      N: n,
      r: p.r,
      p: p.p,
      dkLen: p.dklen,
    });
  }
  if (kdf === "pbkdf2") {
    const p = kdfparams as Pbkdf2KdfParams;
    if (p.prf !== "hmac-sha256") {
      throw new Error(`unsupported pbkdf2 prf: ${p.prf}`);
    }
    return pbkdf2(sha256, pwBytes, salt, { c: p.c, dkLen: p.dklen });
  }
  throw new Error(`unsupported kdf: ${kdf}`);
}

function computeMac(dk: Uint8Array, ciphertext: Uint8Array): string {
  const macInput = new Uint8Array(16 + ciphertext.length);
  macInput.set(dk.slice(16, 32), 0);
  macInput.set(ciphertext, 16);
  // keccak256 returns "0x"-prefixed hex; strip for the keystore field.
  return keccak256(macInput).slice(2);
}

/**
 * Encrypt `privateKey` into a Keystore V3 document.
 *
 * Always uses scrypt (n=262144, r=8, p=1, dklen=32, 16-byte random salt) +
 * AES-128-CTR, matching `eth_account.Account.encrypt`'s defaults.
 */
export function encryptKeystoreV3(
  privateKey: Uint8Array,
  password: string,
): KeystoreV3 {
  const salt = randomBytes(16);
  const dk = scrypt(passwordBytes(password), salt, {
    N: SCRYPT_N,
    r: SCRYPT_R,
    p: SCRYPT_P,
    dkLen: DK_LEN,
  });
  const encryptionKey = dk.slice(0, 16);
  const iv = randomBytes(16);
  const ciphertext = ctr(encryptionKey, iv).encrypt(privateKey);
  const mac = computeMac(dk, ciphertext);

  // Derive the address for the informational `address` field.
  const account = privateKeyToAccount(
    `0x${bytesToHex(privateKey)}` as `0x${string}`,
  );

  return {
    version: 3,
    id: randomUuidV4(),
    address: account.address.slice(2),
    crypto: {
      cipher: "aes-128-ctr",
      cipherparams: { iv: bytesToHex(iv) },
      ciphertext: bytesToHex(ciphertext),
      kdf: "scrypt",
      kdfparams: {
        dklen: DK_LEN,
        n: SCRYPT_N,
        r: SCRYPT_R,
        p: SCRYPT_P,
        salt: bytesToHex(salt),
      },
      mac,
    },
  };
}

/**
 * Decrypt a Keystore V3 document, recovering the raw private key.
 *
 * Supports both `kdf: "scrypt"` (the format this module writes) and
 * `kdf: "pbkdf2"` (hmac-sha256) on read, since eth-account / Geth keystores
 * may use either.
 *
 * @throws {Error} `"Failed to decrypt keystore (wrong password?): MAC mismatch"`
 *   when the derived MAC does not match the stored one (wrong password or
 *   corrupted file).
 */
export function decryptKeystoreV3(
  keystore: KeystoreV3,
  password: string,
): Uint8Array {
  validateKeystoreForDecrypt(keystore);
  const { crypto: c } = keystore;
  const dk = deriveKey(password, c.kdf, c.kdfparams);
  const ciphertext = hexToBytes(c.ciphertext);
  const computedMac = computeMac(dk, ciphertext);
  if (computedMac.toLowerCase() !== c.mac.toLowerCase()) {
    throw new Error(
      "Failed to decrypt keystore (wrong password?): MAC mismatch",
    );
  }
  const encryptionKey = dk.slice(0, 16);
  const iv = hexToBytes(c.cipherparams.iv);
  return ctr(encryptionKey, iv).decrypt(ciphertext);
}

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
  // Available globally in Node >=20 and browsers; avoids an extra dependency.
  return crypto.randomUUID();
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

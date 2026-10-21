/**
 * Revoking passkey (WebAuthn/P256) sessions (SRC-1634).
 *
 * `@altananetwork/sdk`'s `revokeSession` asks the relay to revoke every
 * session as a secp256k1 key. For a passkey session that key hash was never
 * authorized, so the account rejects the revoke (`KeyDoesNotExist`). These
 * calls revoke the key that was actually granted: the account's own
 * `revoke(keyHash)` under the WebAuthn-P256 key hash, and the KeyStore
 * registry entry when the key is registered (its id does not depend on the
 * key type, so that half of the SDK's revoke was already right).
 */

import {
  encodeAbiParameters,
  encodeFunctionData,
  keccak256,
  parseAbi,
} from "viem";
import type { AltanaCall } from "./types.js";

/** Porto / Ithaca `KeyType.WebAuthnP256`. */
const WEBAUTHN_P256_KEY_TYPE = 1;

export const PASSKEY_REVOKE_ABI = parseAbi([
  "function revoke(bytes32 keyHash)",
  "function revokeKey(address user, bytes32 keyId)",
  "function isValidKey(address user, bytes32 keyId) view returns (bool)",
]);

/**
 * The account's hash of a WebAuthn-P256 key,
 * `keccak256(abi.encode(keyType, keccak256(publicKey)))`, for the flat
 * 64-byte `x || y` key the Altana SDK stores.
 */
export function webAuthnP256KeyHash(publicKey: `0x${string}`): `0x${string}` {
  return keccak256(
    encodeAbiParameters(
      [{ type: "uint8" }, { type: "bytes32" }],
      [WEBAUTHN_P256_KEY_TYPE, keccak256(publicKey)],
    ),
  );
}

/** KeyStore registry id of a key: `keccak256` of its stored public key. */
export function keyStoreKeyId(publicKey: `0x${string}`): `0x${string}` {
  return keccak256(publicKey);
}

/**
 * One atomic batch revoking a passkey session: KeyStore `revokeKey` (only
 * when `registered`; revoking a missing entry would revert the batch) and
 * the account self-call `revoke(webAuthnP256KeyHash)`.
 */
export function passkeyRevokeCalls(
  wallet: `0x${string}`,
  keyStore: `0x${string}`,
  publicKey: `0x${string}`,
  registered: boolean,
): AltanaCall[] {
  const calls: AltanaCall[] = [];
  if (registered) {
    calls.push({
      to: keyStore,
      value: 0n,
      data: encodeFunctionData({
        abi: PASSKEY_REVOKE_ABI,
        functionName: "revokeKey",
        args: [wallet, keyStoreKeyId(publicKey)],
      }),
    });
  }
  calls.push({
    to: wallet,
    value: 0n,
    data: encodeFunctionData({
      abi: PASSKEY_REVOKE_ABI,
      functionName: "revoke",
      args: [webAuthnP256KeyHash(publicKey)],
    }),
  });
  return calls;
}

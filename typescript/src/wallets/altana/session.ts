/**
 * Byte-exact persistence for Altana sessions.
 *
 * The Altana account contract hash-commits a session's `permissions` +
 * `expiry` + role + `publicKey` **byte-exactly** at grant time and compares
 * that hash at every execute. A persisted-then-restored session therefore
 * has to reproduce the exact granted values: a `bigint` silently becoming a
 * `number`, a reordered key, or a normalized address is enough for the
 * on-chain validator to no longer recognize the session (field-tested; see
 * the integration plan's pitfall #3).
 *
 * This module owns that round trip:
 *
 * - `serializeSession` emits a versioned JSON envelope, encoding every
 *   bigint as `{"$bigint":"<decimal>"}` (the only bigint in the structure
 *   is `spend[].limit`) and storing `permissions`/`expiry` verbatim — no
 *   sorting, no normalization.
 * - `deserializeSession` re-parses it, rebuilds the session signer from the
 *   embedded private key via the (lazily imported) Altana SDK, and verifies
 *   the rebuilt signer's public key matches the stored one before handing
 *   the session back.
 *
 * SECURITY: the serialized string CONTAINS THE SESSION PRIVATE KEY. Treat
 * it like any key material — file mode 0600, never commit, never log. Its
 * blast radius is bounded by the on-chain session permissions, but within
 * those bounds it spends real funds.
 *
 * Only `privateKey` session signers are supported: a passkey cannot be
 * exported by design, and an injected signer lives in the user's browser
 * wallet. Both get a descriptive error instead of a silently broken file.
 */

import { loadAltanaSdk } from "./sdkLoader.js";
import type { AltanaSession, AltanaSessionPermissions } from "./types.js";

/** Version stamp of the serialized envelope produced by this module. */
export const ALTANA_SESSION_VERSION = 1;

const NON_PRIVATE_KEY_SIGNER_GUIDANCE =
  "only sessions backed by a 'privateKey' signer can be serialized: a " +
  "passkey cannot be exported by design and an injected signer lives in " +
  "the user's wallet. Grant the agent's session with a privateKey session " +
  "signer (the SDK's default when sessionSigner is omitted), or keep the " +
  "granting process alive instead of persisting.";

/** Internal shape of a privateKey signer — the raw key the SDK attaches. */
interface RawKeySigner {
  type?: unknown;
  publicKey?: unknown;
  _privateKey?: unknown;
}

function bigintReplacer(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") {
    return { $bigint: value.toString(10) };
  }
  return value;
}

function bigintReviver(_key: string, value: unknown): unknown {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length === 1 && keys[0] === "$bigint") {
      const encoded = record.$bigint;
      if (typeof encoded === "string") {
        return BigInt(encoded);
      }
    }
  }
  return value;
}

/**
 * Serialize `session` to a JSON string (see module docstring for the
 * byte-exactness contract and the key-material warning).
 *
 * @throws {Error} if the session signer is not a raw-key `privateKey`
 *   signer (passkey / injected / custom).
 */
export function serializeSession(session: AltanaSession): string {
  const signer = session.signer as unknown as RawKeySigner;
  if (signer.type !== "privateKey" || typeof signer._privateKey !== "string") {
    throw new Error(
      `cannot serialize Altana session (signer type '${String(signer.type)}'): ${NON_PRIVATE_KEY_SIGNER_GUIDANCE}`,
    );
  }
  // Envelope key order is fixed by construction; `permissions` is embedded
  // verbatim so JSON.stringify preserves the granted key order.
  const envelope = {
    version: ALTANA_SESSION_VERSION,
    walletAddress: session.walletAddress,
    publicKey: session.publicKey,
    expiry: session.expiry,
    permissions: session.permissions,
    signer: { type: "privateKey", privateKey: signer._privateKey },
  };
  return JSON.stringify(envelope, bigintReplacer);
}

/**
 * Restore an {@link AltanaSession} from `serializeSession` output.
 *
 * Rebuilds the session signer from the embedded private key (lazily
 * importing the Altana SDK) and refuses to return a session whose rebuilt
 * public key does not match the stored one — a mismatch means the file was
 * corrupted or spliced from two different sessions, and such a session
 * would fail on-chain in a far less diagnosable way.
 */
export async function deserializeSession(
  serialized: string,
): Promise<AltanaSession> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized, bigintReviver);
  } catch (error) {
    throw new Error(
      `not a serialized Altana session (invalid JSON): ${(error as Error).message}`,
      { cause: error },
    );
  }
  if (parsed === null || typeof parsed !== "object") {
    throw new Error("not a serialized Altana session (expected a JSON object)");
  }
  const envelope = parsed as Record<string, unknown>;

  if (envelope.version !== ALTANA_SESSION_VERSION) {
    throw new Error(
      `unsupported Altana session version ${JSON.stringify(envelope.version)} (this SDK reads version ${ALTANA_SESSION_VERSION}). Re-grant the session or upgrade @bnb-chain/bnbagent.`,
    );
  }

  const signerRecord = envelope.signer as Record<string, unknown> | undefined;
  const privateKey = signerRecord?.privateKey;
  if (signerRecord?.type !== "privateKey" || typeof privateKey !== "string") {
    throw new Error(
      `cannot restore Altana session (stored signer type '${String(signerRecord?.type)}'): ${NON_PRIVATE_KEY_SIGNER_GUIDANCE}`,
    );
  }

  const walletAddress = envelope.walletAddress;
  const publicKey = envelope.publicKey;
  const permissions = envelope.permissions;
  const expiry = envelope.expiry;
  if (
    typeof walletAddress !== "string" ||
    typeof publicKey !== "string" ||
    typeof expiry !== "number" ||
    permissions === null ||
    typeof permissions !== "object"
  ) {
    throw new Error(
      "not a serialized Altana session (missing walletAddress/publicKey/permissions/expiry)",
    );
  }

  const sdk = await loadAltanaSdk();
  const signer = sdk.signerFromPrivateKey(privateKey as `0x${string}`);
  if (signer.publicKey.toLowerCase() !== publicKey.toLowerCase()) {
    throw new Error(
      `Altana session integrity check failed: the stored private key does not derive the stored publicKey (${publicKey}). The file was corrupted or assembled from two different sessions — refusing to restore. Re-grant the session.`,
    );
  }

  // Stored (granted) bytes win everywhere: publicKey/permissions/expiry are
  // returned verbatim, the rebuilt signer only supplies signing ability.
  return {
    walletAddress: walletAddress as `0x${string}`,
    signer,
    publicKey: publicKey as `0x${string}`,
    permissions: permissions as AltanaSessionPermissions,
    expiry,
  };
}

/**
 * Characterization test for Altana's public `signOrder` primitive.
 *
 * The negotiation flow signs the UTF-8 `0x…` negotiation-hash string with
 * EIP-191. Altana accepts the resulting 32-byte digest and returns the wrapped
 * session signature that its wallet-level ERC-1271 verifier consumes. This
 * test uses the real vendor SDK and independently recovers the inner signer
 * from the IthacaAccount digest, so a mock cannot make an incompatible
 * signature shape look valid.
 */

import { readFileSync } from "node:fs";
import {
  BNB_TESTNET,
  type Session,
  signOrder,
  signerFromPrivateKey,
} from "@altananetwork/sdk";
import {
  http,
  createPublicClient,
  getAddress,
  hashMessage,
  hashTypedData,
  parseAbi,
  recoverAddress,
  size,
  sliceHex,
} from "viem";
import { describe, expect, it } from "vitest";
import { deserializeSession } from "../src/wallets/altana/session.js";

// Anvil's first documented development key. Test-only and never funded here.
const SESSION_PRIVATE_KEY =
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
const SESSION_ADDRESS = getAddress(
  "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
);
const WALLET_ADDRESS = getAddress("0x1111111111111111111111111111111111111111");
const NEGOTIATION_HASH = `0x${"22".repeat(32)}` as const;
const ERC1271_MAGIC_VALUE = "0x1626ba7e";
const ERC1271_ABI = parseAbi([
  "function isValidSignature(bytes32 hash, bytes signature) view returns (bytes4)",
]);

function makeSession(): Session {
  const signer = signerFromPrivateKey(SESSION_PRIVATE_KEY);
  return {
    walletAddress: WALLET_ADDRESS,
    signer,
    publicKey: signer.publicKey,
    permissions: { calls: [] },
    expiry: 2_000_000_000,
  };
}

describe("Altana signOrder", () => {
  it("wraps the bnbagent EIP-191 negotiation digest for wallet-level ERC-1271 verification", async () => {
    // This is byte-for-byte what viem signMessage(NEGOTIATION_HASH) signs:
    // the UTF-8 `0x…` string, not the raw 32 bytes represented by it.
    const appDigest = hashMessage(NEGOTIATION_HASH);
    expect(appDigest).toBe(
      "0x49d4c1d50ce22680c719e4b76e670399384808e6fb3f649cd025033ce29cbb9a",
    );

    const signature = await signOrder(makeSession(), appDigest);

    // IthacaAccount's externally consumed envelope is:
    // inner secp256k1 signature (65) || session key hash (32) || prehash (1).
    expect(size(signature)).toBe(98);

    const accountDigest = hashTypedData({
      domain: { verifyingContract: WALLET_ADDRESS },
      types: { ERC1271Sign: [{ name: "digest", type: "bytes32" }] },
      primaryType: "ERC1271Sign",
      message: { digest: appDigest },
    });
    const recovered = await recoverAddress({
      hash: accountDigest,
      signature: sliceHex(signature, 0, 65),
    });

    expect(recovered).toBe(SESSION_ADDRESS);
  });

  it.runIf(process.env.ALTANA_SIGN_ORDER_LIVE === "1")(
    "is accepted by a live Altana wallet for an approved checker",
    async () => {
      const serialized = process.env.ALTANA_SESSION
        ? process.env.ALTANA_SESSION
        : process.env.ALTANA_SESSION_FILE
          ? readFileSync(process.env.ALTANA_SESSION_FILE, "utf8")
          : null;
      if (!serialized) {
        throw new Error(
          "ALTANA_SIGN_ORDER_LIVE=1 requires ALTANA_SESSION or ALTANA_SESSION_FILE",
        );
      }
      const checker = process.env.ALTANA_SIGNATURE_CHECKER;
      if (!checker) {
        throw new Error(
          "ALTANA_SIGN_ORDER_LIVE=1 requires the session's approved ALTANA_SIGNATURE_CHECKER",
        );
      }

      const session = await deserializeSession(serialized);
      const appDigest = hashMessage(NEGOTIATION_HASH);
      const signature = await signOrder(session, appDigest);
      const client = createPublicClient({
        transport: http(process.env.RPC_URL ?? BNB_TESTNET.publicRpcUrl),
      });

      // `account` sets eth_call.from: Altana scopes ERC-1271 validation to
      // checker contracts approved for this exact session key.
      const result = await client.readContract({
        address: session.walletAddress,
        abi: ERC1271_ABI,
        functionName: "isValidSignature",
        args: [appDigest, signature],
        account: getAddress(checker),
      });

      expect(result).toBe(ERC1271_MAGIC_VALUE);
    },
  );
});

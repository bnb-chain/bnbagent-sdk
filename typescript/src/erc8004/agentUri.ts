/**
 * Agent URI generation utility.
 *
 * Generates EIP-8004 compliant agent registration files and agent URIs.
 * Port of `python/bnbagent/erc8004/agent_uri.py`.
 */

import { canonicalJson, keccakOfCanonicalJson } from "../core/canonicalJson.js";
import type { AgentEndpoint } from "./models.js";

const DATA_URI_PREFIX = "data:application/json;base64,";

/** Options accepted by {@link AgentURIGenerator.generateRegistrationFile}. */
export interface GenerateRegistrationFileOpts {
  name: string;
  description: string;
  endpoints: Array<AgentEndpoint | Record<string, unknown>>;
  image?: string | null;
  /**
   * On-chain agent id. Accepts `bigint` (viem decodes uint256 token ids as
   * bigint) and is coerced to a JSON-serializable number — `canonicalJson`
   * cannot serialize a bigint. ERC-8004 token ids are sequential, so the
   * `Number()` coercion is safe in practice; a value above 2^53 would lose
   * precision.
   */
  agentId?: number | bigint | null;
  identityRegistry?: string | null;
  chainId?: number | null;
  supportedTrust?: string[] | null;
}

function endpointToDict(
  endpoint: AgentEndpoint | Record<string, unknown>,
): Record<string, unknown> {
  const maybeToDict = (endpoint as { toDict?: unknown }).toDict;
  if (typeof maybeToDict === "function") {
    return (endpoint as AgentEndpoint).toDict();
  }
  if (endpoint && typeof endpoint === "object") {
    return endpoint as Record<string, unknown>;
  }
  throw new TypeError(
    `Expected AgentEndpoint or object, got ${typeof endpoint}`,
  );
}

/**
 * Generator for EIP-8004 compliant agent registration files and agent URIs.
 *
 * All methods are static (a namespace object, mirroring the Python
 * classmethod-only `AgentURIGenerator`).
 */
export const AgentURIGenerator = {
  /**
   * Generate an EIP-8004 compliant agent registration file.
   *
   * `registrations` is populated only when `agentId`, `identityRegistry` and
   * `chainId` are ALL present; otherwise it is an empty array.
   */
  generateRegistrationFile(
    opts: GenerateRegistrationFileOpts,
  ): Record<string, unknown> {
    const {
      name,
      description,
      endpoints,
      image = null,
      agentId = null,
      identityRegistry = null,
      chainId = null,
      supportedTrust = null,
    } = opts;

    if (!name || !description) {
      throw new Error("name and description are required");
    }
    if (!endpoints || endpoints.length === 0) {
      throw new Error(
        "endpoints is required and must contain at least one endpoint",
      );
    }

    const endpointDicts = endpoints.map(endpointToDict);

    const registrations: Record<string, unknown>[] = [];
    if (
      agentId !== null &&
      agentId !== undefined &&
      identityRegistry &&
      chainId !== null &&
      chainId !== undefined
    ) {
      registrations.push({
        // Coerce bigint → number so the registration file stays
        // canonicalJson-serializable (JSON.stringify throws on bigint).
        agentId: typeof agentId === "bigint" ? Number(agentId) : agentId,
        agentRegistry: `eip155:${chainId}:${identityRegistry}`,
      });
    }

    const registrationFile: Record<string, unknown> = {
      type: "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
      name,
      description,
      image: image || "",
      services: endpointDicts,
      registrations,
    };

    if (supportedTrust && supportedTrust.length > 0) {
      registrationFile.supportedTrust = supportedTrust;
    }

    return registrationFile;
  },

  /**
   * Calculate the hash of a registration file: keccak256 of its canonical
   * JSON serialization (byte-identical to Python's
   * `json.dumps(x, sort_keys=True, separators=(",", ":"))`).
   */
  calculateFileHash(registrationFile: Record<string, unknown>): `0x${string}` {
    return keccakOfCanonicalJson(registrationFile);
  },

  /**
   * Generate the agent URI for an agent registration.
   *
   * Always returns a base64 data URI (`data:application/json;base64,...`).
   */
  generateAgentUri(opts: GenerateRegistrationFileOpts): string {
    const registrationFile = AgentURIGenerator.generateRegistrationFile(opts);
    const base64Str =
      AgentURIGenerator.encodeRegistrationFileToBase64(registrationFile);
    return `${DATA_URI_PREFIX}${base64Str}`;
  },

  /**
   * Encode a registration file to a base64 string of its canonical JSON
   * serialization. NOT a plain `JSON.stringify` — this must be
   * byte-identical to the Python SDK's encoding for cross-SDK
   * interoperability.
   */
  encodeRegistrationFileToBase64(
    registrationFile: Record<string, unknown>,
  ): string {
    return Buffer.from(canonicalJson(registrationFile), "utf-8").toString(
      "base64",
    );
  },

  /**
   * Decode a base64 string (with or without the `data:` URI prefix) back to
   * a registration file object.
   */
  decodeRegistrationFileFromBase64(base64Str: string): Record<string, unknown> {
    const stripped = base64Str.startsWith(DATA_URI_PREFIX)
      ? base64Str.slice(DATA_URI_PREFIX.length)
      : base64Str;
    const json = Buffer.from(stripped, "base64").toString("utf-8");
    return JSON.parse(json);
  },
};

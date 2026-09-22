/**
 * ERC8004Agent SDK - Main SDK Class
 *
 * Provides a high-level interface for on-chain agent registration and
 * management. Handles wallet management, contract interactions, and
 * provides convenient methods for common operations.
 *
 * Port of `python/bnbagent/erc8004/agent.py`.
 */

import {
  type PublicClient,
  createPublicClient,
  http as httpTransport,
} from "viem";
import type { NetworkConfig } from "../config.js";
import { SCAN_API_URL } from "../constants.js";
import { Paymaster } from "../core/paymaster.js";
import { describeError } from "../core/txSender.js";
import {
  ERC8004PartialRegistrationError,
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../errors.js";
import { fetchPublicJson } from "../utils/publicHttp.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { AgentURIGenerator } from "./agentUri.js";
import { type Erc8004Config, getErc8004Config } from "./constants.js";
import {
  ContractInterface,
  type MetadataEntry,
  type RegisterAgentResult,
  type WriteResult,
} from "./contract.js";
import { AgentEndpoint } from "./models.js";

// ── ERC8004Agent ───────────────────────────────────────────────────────────

/** Options accepted by {@link ERC8004Agent.create}. */
export interface CreateErc8004AgentOpts {
  walletProvider: WalletProvider;
  network?: string | NetworkConfig;
  debug?: boolean;
}

/** Options accepted by {@link ERC8004Agent.generateAgentUri}. */
export interface GenerateAgentUriOpts {
  name: string;
  description: string;
  endpoints: AgentEndpoint[];
  image?: string | null;
  /** On-chain agent id; accepts `bigint` and is coerced to a number (see
   * {@link GenerateRegistrationFileOpts.agentId}). */
  agentId?: number | bigint | null;
  supportedTrust?: string[] | null;
}

/** Result of {@link ERC8004Agent.registerAgent}. */
export interface AgentRegisterResult extends RegisterAgentResult {
  agentURI: string;
}

/** Result of {@link ERC8004Agent.setAgentUri}. */
export interface AgentSetUriResult extends WriteResult {
  agentURI: string;
}

/** Result of {@link ERC8004Agent.getLocalAgentInfo}. */
export interface LocalAgentInfo {
  name: string;
  agentId: number;
  agentUri: string;
  ownerAddress: string;
}

/**
 * Main SDK class for ERC-8004 on-chain agent operations.
 *
 * Construct via the async {@link ERC8004Agent.create} factory — the
 * defense-in-depth chain-id assertion at startup requires an RPC
 * round-trip, which a synchronous constructor cannot perform.
 */
export class ERC8004Agent {
  private readonly walletProviderRef: WalletProvider;
  private readonly networkConfig: Erc8004Config;
  private readonly client: PublicClient;
  private readonly contractInterface: ContractInterface;
  private readonly debug: boolean;

  private constructor(
    walletProvider: WalletProvider,
    networkConfig: Erc8004Config,
    client: PublicClient,
    contractInterface: ContractInterface,
    debug: boolean,
  ) {
    this.walletProviderRef = walletProvider;
    this.networkConfig = networkConfig;
    this.client = client;
    this.contractInterface = contractInterface;
    this.debug = debug;
  }

  /**
   * Create an `ERC8004Agent`.
   *
   * Connects to the network's RPC and asserts its `chain_id` matches the
   * resolved network config (defense-in-depth against a misconfigured or
   * maliciously redirected `RPC_URL`) before returning.
   *
   * @throws {Error} if `walletProvider` is missing, the network is unknown,
   *   the registry contract address is missing, the RPC is unreachable, or
   *   the RPC's chain_id does not match the expected network.
   */
  static async create(opts: CreateErc8004AgentOpts): Promise<ERC8004Agent> {
    const { walletProvider, network = "bsc-testnet", debug = false } = opts;
    if (!walletProvider) {
      throw new Error(
        "wallet_provider is required. Use EVMWalletProvider(password='...') for private key wallets.",
      );
    }

    const networkConfig = getErc8004Config(network);
    if (!networkConfig.registryContract) {
      throw new Error(
        `registry_contract not found in ${networkConfig.name} config`,
      );
    }

    const client = createPublicClient({
      transport: httpTransport(networkConfig.rpcUrl),
    });

    let actualChainId: number;
    try {
      actualChainId = await client.getChainId();
    } catch (error) {
      throw new Error(
        `Failed to connect to RPC: ${networkConfig.rpcUrl} (${describeError(error)})`,
        { cause: error },
      );
    }

    if (
      networkConfig.chainId !== null &&
      networkConfig.chainId !== undefined &&
      actualChainId !== networkConfig.chainId
    ) {
      throw new Error(
        `RPC chain_id mismatch for network '${networkConfig.name}': ` +
          `expected ${networkConfig.chainId}, got ${actualChainId}. ` +
          `The RPC at ${networkConfig.rpcUrl} is serving a different chain.`,
      );
    }

    let paymaster: Paymaster | null = null;
    if (networkConfig.paymaster) {
      if (!networkConfig.paymasterUrl) {
        throw new Error(
          `paymaster_url not found in ${networkConfig.name} config. Paymaster is required for this network.`,
        );
      }
      paymaster = new Paymaster(networkConfig.paymasterUrl, debug);
    }

    const contractInterface = new ContractInterface({
      client,
      contractAddress: networkConfig.registryContract,
      walletProvider,
      paymaster,
    });

    return new ERC8004Agent(
      walletProvider,
      networkConfig,
      client,
      contractInterface,
      debug,
    );
  }

  /**
   * Generate an agent URI for agent registration.
   *
   * Creates an EIP-8004 compliant agent registration file and returns a
   * base64 data URI. To avoid re-registering, check local state with
   * `getLocalAgentInfo(name)` first.
   */
  generateAgentUri(opts: GenerateAgentUriOpts): string {
    if (!opts.endpoints || opts.endpoints.length === 0) {
      throw new Error(
        "endpoints is required and must contain at least one endpoint",
      );
    }
    return AgentURIGenerator.generateAgentUri({
      name: opts.name,
      description: opts.description,
      image: opts.image ?? null,
      endpoints: opts.endpoints,
      agentId: opts.agentId ?? null,
      identityRegistry: this.contractInterface.address,
      chainId: this.networkConfig.chainId,
      supportedTrust: opts.supportedTrust ?? null,
    });
  }

  /**
   * Find an agent registered by this wallet, by name.
   *
   * Queries the indexer API (via `getAllAgents`) and returns the first
   * agent whose name matches and whose owner is this wallet's address.
   * Returns `null` when not found or the lookup fails.
   */
  async getLocalAgentInfo(name: string): Promise<LocalAgentInfo | null> {
    if (!name) {
      return null;
    }
    try {
      const myAddress = this.walletAddress.toLowerCase();
      const result = await this.getAllAgents(100, 0);
      const items = (result.items as Array<Record<string, unknown>>) ?? [];
      for (const agent of items) {
        const ownerAddress = String(agent.owner_address ?? "").toLowerCase();
        const agentName = String(agent.name ?? "").toLowerCase();
        if (ownerAddress === myAddress && agentName === name.toLowerCase()) {
          return {
            name: agent.name as string,
            agentId: Number(agent.token_id),
            agentUri: (agent.agent_uri as string | undefined) ?? "",
            ownerAddress: agent.owner_address as string,
          };
        }
      }
      return null;
    } catch {
      return null;
    }
  }

  /**
   * Register a new agent on-chain.
   *
   * Two-phase: (1) `register` on the contract; (2) if an `agentId` was
   * assigned and the parsed agent data carries endpoints, regenerate the
   * agent URI WITH `agentId`/registry/chainId and push it via
   * `setAgentUri`. A phase-2 failure raises
   * {@link ERC8004PartialRegistrationError} (the agent already exists —
   * only the registrations field failed to update); `txHash` is populated
   * only when the cause is a {@link TransactionPendingError}.
   */
  async registerAgent(
    agentUri: string,
    metadata?: MetadataEntry[] | null,
  ): Promise<AgentRegisterResult> {
    if (!agentUri) {
      throw new Error("agent_uri is required");
    }

    const agentData = await ERC8004Agent.parseAgentUri(agentUri);
    if (!agentData) {
      throw new Error("Failed to parse agent URI");
    }
    const agentName = agentData.name;
    if (!agentName) {
      throw new Error("Agent URI does not contain a name field");
    }

    const result = await this.contractInterface.registerAgent(
      agentUri,
      metadata,
    );
    const agentId = result.agentId;

    let finalAgentUri = agentUri;
    if (agentId !== null && agentId !== undefined) {
      try {
        const services =
          (agentData.services as Array<Record<string, unknown>>) ?? [];
        const endpoints = services.map(
          (svc) =>
            new AgentEndpoint({
              name: (svc.name as string) ?? "",
              endpoint: (svc.endpoint as string) ?? "",
              version: (svc.version as string | undefined) ?? null,
            }),
        );

        if (endpoints.length > 0) {
          const supportedTrust =
            (agentData.supportedTrust as string[] | undefined) ??
            (agentData.supportedTrusts as string[] | undefined) ??
            null;
          finalAgentUri = this.generateAgentUri({
            name: (agentData.name as string) ?? "",
            description: (agentData.description as string) ?? "",
            image: (agentData.image as string | undefined) ?? null,
            endpoints,
            agentId,
            supportedTrust,
          });

          await this.contractInterface.setAgentUri(agentId, finalAgentUri);
        }
      } catch (error) {
        // register confirmed (agentId assigned) but the URI-completion
        // step failed (revert), is broadcast-yet-unconfirmed (pending), or
        // returned a relay hash the chain never observed. Either way, the
        // agent exists; only the URI completion is partial.
        const txHash =
          error instanceof TransactionPendingError ? error.txHash : null;
        const retryable = !(error instanceof RelaySubmissionUnverifiedError);
        throw new ERC8004PartialRegistrationError(
          agentId,
          finalAgentUri,
          error,
          txHash,
          retryable,
        );
      }
    }

    return { ...result, agentURI: finalAgentUri };
  }

  /** Get information about a registered agent. */
  async getAgentInfo(agentId: number) {
    return this.contractInterface.getAgentInfo(agentId);
  }

  /**
   * List all registered agents via the 8004scan indexer API. Does not
   * require on-chain calls.
   *
   * `limit` is capped at 100.
   */
  async getAllAgents(limit = 10, offset = 0): Promise<Record<string, unknown>> {
    const chainId = this.networkConfig.chainId;
    const params = new URLSearchParams({
      chain_id: String(chainId),
      limit: String(Math.min(limit, 100)),
      offset: String(offset),
    });

    let response: Response;
    try {
      response = await fetch(`${SCAN_API_URL}/agents?${params.toString()}`, {
        signal: AbortSignal.timeout(30_000),
      });
    } catch (error) {
      throw new Error(`8004scan API request failed: ${describeError(error)}`, {
        cause: error,
      });
    }

    if (!response.ok) {
      throw new Error(
        `8004scan API request failed: ${response.status} ${response.statusText}`,
      );
    }

    return (await response.json()) as Record<string, unknown>;
  }

  /** Get a metadata value for an agent, decoded from bytes. */
  async getMetadata(agentId: number, key: string): Promise<string> {
    return this.contractInterface.getMetadata(agentId, key);
  }

  /** Set metadata for an agent (must be owner or operator). */
  async setMetadata(
    agentId: number,
    key: string,
    value: string,
  ): Promise<WriteResult> {
    return this.contractInterface.setMetadata(agentId, key, value);
  }

  /** Set the agent URI for an agent. */
  async setAgentUri(
    agentId: number,
    agentUri: string,
  ): Promise<AgentSetUriResult> {
    if (!agentUri) {
      throw new Error("agent_uri is required");
    }
    const result = await this.contractInterface.setAgentUri(agentId, agentUri);
    return { ...result, agentURI: agentUri };
  }

  /**
   * Parse an agent URI to a plain object.
   *
   * Supports:
   * - Base64 data URI: `data:application/json;base64,...` — decodes and parses.
   * - HTTP/HTTPS URL — fetches and parses JSON, behind an SSRF guard
   *   (blocked cloud-metadata hostnames, private/loopback/link-local/
   *   reserved/CGNAT IP ranges, no redirects, 10s timeout, 1 MB cap).
   *
   * Never throws: any parsing/format/network failure resolves to `null`.
   */
  static async parseAgentUri(
    agentUri: string,
  ): Promise<Record<string, unknown> | null> {
    if (!agentUri) {
      return null;
    }

    if (agentUri.startsWith("data:application/json;base64,")) {
      try {
        return AgentURIGenerator.decodeRegistrationFileFromBase64(agentUri);
      } catch {
        return null;
      }
    }

    if (agentUri.startsWith("http://") || agentUri.startsWith("https://")) {
      return fetchPublicJson(agentUri);
    }

    return null;
  }

  /** The wallet's on-chain address. */
  get walletAddress(): string {
    return this.walletProviderRef.address;
  }

  /** The ERC-8004 Identity Registry contract address. */
  get contractAddress(): string {
    return this.contractInterface.address;
  }

  /** The resolved network configuration. */
  get network(): Erc8004Config {
    return this.networkConfig;
  }
}

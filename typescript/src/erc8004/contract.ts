/**
 * Contract Interface Module
 *
 * Handles interactions with the ERC-8004 Identity Registry smart contract.
 * Provides methods for registering agents and querying agent information.
 *
 * Port of `python/bnbagent/erc8004/contract.py`.
 */

import {
  type Hex,
  type PublicClient,
  type TransactionReceipt,
  decodeEventLog,
  getAddress,
  hexToString,
  toBytes,
  toHex,
} from "viem";
import { identityRegistryAbi } from "../abis/identityRegistry.js";
import { ContractBase } from "../core/contractBase.js";
import type { Paymaster } from "../core/paymaster.js";
import { describeError } from "../core/txSender.js";
import {
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../errors.js";
import {
  ERC8004_REGISTER,
  ERC8004_SET_AGENT_URI,
  ERC8004_SET_METADATA,
  type Intent,
} from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { BUILT_WITH_KEY, getBuiltWithValue } from "./constants.js";

/** A single metadata entry as passed by callers (string key/value). */
export interface MetadataEntry {
  key: string;
  value: string;
}

/** Options accepted by the {@link ContractInterface} constructor. */
export interface ContractInterfaceOpts {
  client: PublicClient;
  contractAddress: string;
  walletProvider?: WalletProvider | null;
  paymaster?: Paymaster | null;
  /**
   * Seconds to wait for a transaction receipt. `null`/absent (default) uses
   * the SDK default (tunable via `setDefaultReceiptTimeout`).
   */
  receiptTimeout?: number | null;
}

/** Result of {@link ContractInterface.registerAgent}. */
export interface RegisterAgentResult {
  success: true;
  transactionHash: Hex;
  agentId: number | null;
  receipt: TransactionReceipt | null;
}

/** Result of {@link ContractInterface.getAgentInfo}. */
export interface AgentInfo {
  agentId: number;
  agentAddress: `0x${string}`;
  agentWallet: `0x${string}`;
  owner: `0x${string}`;
  agentURI: string;
}

/** Result of a plain write (set metadata / set agent URI). */
export interface WriteResult {
  success: true;
  transactionHash: Hex;
  receipt: TransactionReceipt | null;
}

/**
 * Interface for interacting with the ERC-8004 Identity Registry contract.
 *
 * Provides methods for registering agents, getting agent information, and
 * setting/getting metadata. Writes go through `ContractBase`'s intent
 * execution seam (`executeIntent`), so the wallet decides how each intent is
 * built/signed/broadcast (local signer vs. self-broadcasting backend).
 */
export class ContractInterface extends ContractBase {
  constructor(opts: ContractInterfaceOpts) {
    super({
      client: opts.client,
      address: getAddress(opts.contractAddress),
      abi: identityRegistryAbi,
      walletProvider: opts.walletProvider ?? null,
      paymaster: opts.paymaster ?? null,
      receiptTimeout: opts.receiptTimeout ?? null,
    });
  }

  /**
   * Auto-inject a `built_with` metadata entry identifying this SDK unless
   * the caller already supplied one.
   */
  private injectBuiltWith(metadata?: MetadataEntry[] | null): MetadataEntry[] {
    const items = metadata ? [...metadata] : [];
    if (!items.some((entry) => entry.key === BUILT_WITH_KEY)) {
      items.push({ key: BUILT_WITH_KEY, value: getBuiltWithValue() });
    }
    return items;
  }

  /**
   * Try to recover the `agentId` assigned by a `register` call from the
   * `Registered` event in the transaction's logs. Returns `null` if no log
   * decodes as `Registered`.
   *
   * Filters by `log.address` and re-checks `decoded.eventName` for the same
   * reason `CommerceClient.parseJobCreatedId` does: viem's `decodeEventLog`
   * is not bound to a contract instance (unlike web3.py's
   * `process_receipt`), so without the address filter a same-topic0 log
   * emitted by an UNRELATED contract in the same receipt (e.g. a bundled
   * multicall or AA-paymaster relay) could be decoded and its `agentId`
   * returned — silently binding the agent to the wrong on-chain token id.
   */
  private parseRegisteredAgentId(
    logs: TransactionReceipt["logs"],
  ): number | null {
    const address = this.address.toLowerCase();
    for (const log of logs) {
      if (log.address.toLowerCase() !== address) {
        continue;
      }
      try {
        const decoded = decodeEventLog({
          abi: identityRegistryAbi,
          eventName: "Registered",
          data: log.data,
          topics: log.topics,
        });
        if (decoded.eventName !== "Registered") {
          continue;
        }
        const agentId = (decoded.args as { agentId?: bigint }).agentId;
        if (agentId !== undefined) {
          return Number(agentId);
        }
      } catch {
        // Not a Registered log (or a different event sharing no topic0
        // match) — try the next one.
      }
    }
    return null;
  }

  /** Register a new agent on-chain. */
  async registerAgent(
    agentUri: string,
    metadata?: MetadataEntry[] | null,
  ): Promise<RegisterAgentResult> {
    try {
      const fullMetadata = this.injectBuiltWith(metadata);
      const metadataBytes = fullMetadata.map((entry) => ({
        metadataKey: entry.key,
        metadataValue: toHex(toBytes(entry.value)),
      }));

      const intent: Intent = {
        name: ERC8004_REGISTER,
        kwargs: { agentUri, metadata: fullMetadata },
        call: {
          address: this.address,
          abi: this.abi,
          functionName: "register",
          args: [agentUri, metadataBytes],
        },
        description: "registration",
      };
      const result = await this.executeIntent(intent);

      let agentId = (result.agentId as number | null | undefined) ?? null;
      if (agentId == null && result.receipt?.logs?.length) {
        agentId = this.parseRegisteredAgentId(result.receipt.logs);
      }

      return {
        success: true,
        transactionHash: result.transactionHash,
        agentId,
        receipt: result.receipt,
      };
    } catch (error) {
      if (
        error instanceof TransactionPendingError ||
        error instanceof RelaySubmissionUnverifiedError
      ) {
        // Preserve transaction-state signals (and their txHash) so callers
        // can reconcile instead of blindly retrying the registration.
        throw error;
      }
      throw new Error(`Agent registration failed: ${describeError(error)}`, {
        cause: error,
      });
    }
  }

  /** Get information about an agent. */
  async getAgentInfo(agentId: number): Promise<AgentInfo> {
    try {
      const id = BigInt(agentId);
      const agentWallet = await this.callWithRetry(() =>
        this.client.readContract({
          address: this.address,
          abi: identityRegistryAbi,
          functionName: "getAgentWallet",
          args: [id],
        }),
      );
      const owner = await this.callWithRetry(() =>
        this.client.readContract({
          address: this.address,
          abi: identityRegistryAbi,
          functionName: "ownerOf",
          args: [id],
        }),
      );
      const agentURI = await this.callWithRetry(() =>
        this.client.readContract({
          address: this.address,
          abi: identityRegistryAbi,
          functionName: "tokenURI",
          args: [id],
        }),
      );

      return {
        agentId,
        agentAddress: agentWallet,
        agentWallet,
        owner,
        agentURI,
      };
    } catch (error) {
      throw new Error(`Failed to get agent info: ${describeError(error)}`, {
        cause: error,
      });
    }
  }

  /** Get metadata for an agent, decoded from bytes to a UTF-8 string. */
  async getMetadata(agentId: number, key: string): Promise<string> {
    try {
      const valueHex = await this.callWithRetry(() =>
        this.client.readContract({
          address: this.address,
          abi: identityRegistryAbi,
          functionName: "getMetadata",
          args: [BigInt(agentId), key],
        }),
      );
      return hexToString(valueHex as Hex);
    } catch (error) {
      throw new Error(`Failed to get metadata: ${describeError(error)}`, {
        cause: error,
      });
    }
  }

  /** Set metadata for an agent (must be owner or operator). */
  async setMetadata(
    agentId: number,
    key: string,
    value: string,
  ): Promise<WriteResult> {
    try {
      const valueBytes = toHex(toBytes(value));
      const intent: Intent = {
        name: ERC8004_SET_METADATA,
        kwargs: { agentId, key, value },
        call: {
          address: this.address,
          abi: this.abi,
          functionName: "setMetadata",
          args: [BigInt(agentId), key, valueBytes],
        },
        description: "set metadata",
      };
      const result = await this.executeIntent(intent);
      return {
        success: true,
        transactionHash: result.transactionHash,
        receipt: result.receipt,
      };
    } catch (error) {
      if (error instanceof RelaySubmissionUnverifiedError) {
        throw error;
      }
      // NOTE: unlike registerAgent/setAgentUri, the Python reference does
      // NOT special-case TransactionPendingError here — it wraps every
      // failure (including a pending receipt) into a plain error. Ported
      // as-is for parity; see task-17-report.md.
      throw new Error(`Failed to set metadata: ${describeError(error)}`, {
        cause: error,
      });
    }
  }

  /**
   * Set the agent URI using the contract's `setAgentURI` function, which
   * updates the tokenURI directly per the EIP-8004 specification.
   */
  async setAgentUri(agentId: number, agentUri: string): Promise<WriteResult> {
    try {
      const intent: Intent = {
        name: ERC8004_SET_AGENT_URI,
        kwargs: { agentId, agentUri },
        call: {
          address: this.address,
          abi: this.abi,
          functionName: "setAgentURI",
          args: [BigInt(agentId), agentUri],
        },
        description: "set agent URI",
      };
      const result = await this.executeIntent(intent);
      return {
        success: true,
        transactionHash: result.transactionHash,
        receipt: result.receipt,
      };
    } catch (error) {
      if (
        error instanceof TransactionPendingError ||
        error instanceof RelaySubmissionUnverifiedError
      ) {
        throw error;
      }
      throw new Error(`Failed to set agent URI: ${describeError(error)}`, {
        cause: error,
      });
    }
  }
}

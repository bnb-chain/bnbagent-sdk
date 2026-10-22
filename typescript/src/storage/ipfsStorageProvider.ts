/**
 * IPFSStorageProvider — IPFS pinning service storage.
 *
 * Uses an HTTP pinning API (Pinata-compatible `pinJSONToIPFS`) for upload
 * and an IPFS gateway for download/exists. Uses the global `fetch` (Node
 * 20+ has it built in) rather than a dependency, since the TS SDK has no
 * `httpx` equivalent to reach for.
 *
 * Port of `python/bnbagent/storage/ipfs_storage_provider.py`.
 */

import { getEnv } from "../core/envUtil.js";
import { StorageError } from "../errors.js";
import { StorageProvider } from "./storageProvider.js";

const DEFAULT_PINNING_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS";
const DEFAULT_GATEWAY_URL = "https://gateway.pinata.cloud/ipfs/";
const DEFAULT_MAX_UPLOAD_BYTES = 8 * 1024 * 1024;

// CIDv0: Qm + 44 base58 chars; CIDv1: b + base32 (58+ chars)
const CID_RE = /^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})$/;

/**
 * IPFS storage via HTTP pinning API.
 *
 * @param pinningApiUrl - e.g. "https://api.pinata.cloud/pinning/pinJSONToIPFS"
 * @param pinningApiKey - Bearer token (JWT) for the pinning service
 * @param gatewayUrl - e.g. "https://gateway.pinata.cloud/ipfs/"
 * @param maxUploadBytes - Serialized pin request ceiling (default 8 MiB)
 */
export class IPFSStorageProvider extends StorageProvider {
  private readonly pinningUrl: string;
  private readonly apiKey: string;
  private readonly gateway: string;
  private readonly maxUploadBytes: number;

  constructor(
    pinningApiUrl: string,
    pinningApiKey: string,
    gatewayUrl: string = DEFAULT_GATEWAY_URL,
    maxUploadBytes: number = DEFAULT_MAX_UPLOAD_BYTES,
    private readonly maxDownloadBytes: number = 8 * 1024 * 1024,
    private readonly downloadTimeoutMs: number = 30_000,
  ) {
    super();
    if (!Number.isInteger(maxUploadBytes) || maxUploadBytes <= 0) {
      throw new Error("maxUploadBytes must be a positive integer");
    }
    this.pinningUrl = pinningApiUrl;
    this.apiKey = pinningApiKey;
    this.gateway = gatewayUrl.replace(/\/+$/, "");
    this.maxUploadBytes = maxUploadBytes;
    if (!Number.isSafeInteger(maxDownloadBytes) || maxDownloadBytes <= 0)
      throw new Error("maxDownloadBytes must be a positive integer");
    if (!Number.isFinite(downloadTimeoutMs) || downloadTimeoutMs <= 0)
      throw new Error("downloadTimeoutMs must be positive and finite");
  }

  static fromEnv(): IPFSStorageProvider {
    const apiKey = getEnv("STORAGE_API_KEY");
    if (!apiKey) {
      throw new Error("STORAGE_API_KEY required for IPFSStorageProvider");
    }
    return new IPFSStorageProvider(
      getEnv("STORAGE_API_URL", DEFAULT_PINNING_URL) as string,
      apiKey,
      getEnv("STORAGE_GATEWAY_URL", DEFAULT_GATEWAY_URL) as string,
      IPFSStorageProvider.maxUploadBytesFromEnv(),
      Number(getEnv("STORAGE_MAX_DOWNLOAD_BYTES", String(8 * 1024 * 1024))),
    );
  }

  async upload(
    data: Record<string, unknown>,
    filename?: string,
  ): Promise<string> {
    const headers = {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
    };

    let pinName: string;
    if (filename) {
      pinName = filename.replaceAll(".json", "");
    } else {
      const jobId = IPFSStorageProvider.extractJobId(data);
      pinName = jobId ? `erc8183-job-${jobId}` : "deliverable";
    }

    const payload = {
      pinataContent: data,
      pinataMetadata: { name: pinName },
    };
    const body = JSON.stringify(payload);
    const bodyBytes = Buffer.byteLength(body);
    if (bodyBytes > this.maxUploadBytes) {
      throw new StorageError(
        `IPFS upload payload is ${bodyBytes} bytes; limit is ${this.maxUploadBytes} bytes`,
      );
    }

    const resp = await fetch(this.pinningUrl, {
      method: "POST",
      headers,
      body,
      signal: AbortSignal.timeout(60_000),
    });
    if (!resp.ok) {
      throw new StorageError(
        `Pinning request failed: ${resp.status} ${resp.statusText}`,
      );
    }
    const result = (await resp.json()) as Record<string, unknown>;
    const cid =
      (result.IpfsHash as string | undefined) ??
      (result.cid as string | undefined);
    if (!cid) {
      throw new StorageError(
        `Unexpected pinning response: ${JSON.stringify(result)}`,
      );
    }

    const ipfsUrl = `ipfs://${cid}`;
    return ipfsUrl;
  }

  async download(url: string): Promise<Record<string, unknown>> {
    const cid = IPFSStorageProvider.extractCid(url);
    const gatewayUrl = `${this.gateway}/${cid}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.downloadTimeoutMs);
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const abort = () => {
      void reader?.cancel().catch(() => {});
    };
    controller.signal.addEventListener("abort", abort, { once: true });
    try {
      const resp = await fetch(gatewayUrl, {
        signal: controller.signal,
        redirect: "error",
      });
      if (!resp.ok) {
        throw new StorageError(
          `Gateway request failed: ${resp.status} ${resp.statusText}`,
        );
      }
      const length = Number(resp.headers.get("content-length"));
      if (length > this.maxDownloadBytes) {
        await resp.body?.cancel();
        throw new StorageError("IPFS download exceeds byte limit");
      }
      if (!resp.body) throw new StorageError("IPFS download has no body");
      reader = resp.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (controller.signal.aborted)
          throw new StorageError("IPFS download timed out");
        if (done) break;
        size += value.byteLength;
        if (size > this.maxDownloadBytes)
          throw new StorageError("IPFS download exceeds byte limit");
        chunks.push(value);
      }
      const data: unknown = JSON.parse(
        Buffer.concat(chunks, size).toString("utf8"),
      );
      if (!data || typeof data !== "object" || Array.isArray(data))
        throw new StorageError("IPFS download must be a JSON object");
      return data as Record<string, unknown>;
    } finally {
      clearTimeout(timer);
      controller.signal.removeEventListener("abort", abort);
      await reader?.cancel().catch(() => {});
    }
  }

  async exists(url: string): Promise<boolean> {
    const cid = IPFSStorageProvider.extractCid(url);
    const gatewayUrl = `${this.gateway}/${cid}`;

    try {
      const resp = await fetch(gatewayUrl, {
        method: "HEAD",
        signal: AbortSignal.timeout(10_000),
      });
      return resp.status === 200;
    } catch {
      return false;
    }
  }

  /** Convert an `ipfs://` URL to an HTTP gateway URL for browser access. */
  getGatewayUrl(ipfsUrl: string): string {
    const cid = IPFSStorageProvider.extractCid(ipfsUrl);
    return `${this.gateway}/${cid}`;
  }

  static extractCid(url: string): string {
    let cid: string;
    if (url.startsWith("ipfs://")) {
      cid = url.slice(7);
    } else if (url.includes("/ipfs/")) {
      cid = url.split("/ipfs/").pop() as string;
    } else {
      cid = url;
    }
    cid = cid.replace(/^\/+|\/+$/g, "");
    if (!CID_RE.test(cid)) {
      throw new StorageError(`Invalid IPFS CID format: ${cid}`);
    }
    return cid;
  }

  private static extractJobId(data: Record<string, unknown>): unknown {
    const job = data.job;
    if (job && typeof job === "object" && !Array.isArray(job)) {
      return (job as Record<string, unknown>).id;
    }
    return undefined;
  }

  private static maxUploadBytesFromEnv(): number {
    const raw = getEnv("STORAGE_MAX_UPLOAD_BYTES");
    if (!raw) {
      return DEFAULT_MAX_UPLOAD_BYTES;
    }
    const value = Number(raw);
    return Number.isInteger(value) && value > 0
      ? value
      : DEFAULT_MAX_UPLOAD_BYTES;
  }
}

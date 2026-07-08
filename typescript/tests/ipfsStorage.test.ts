/**
 * Tests for IPFSStorageProvider — IPFS pinning service storage.
 *
 * Ports `python/tests/test_ipfs_storage.py`. `fetch` is mocked via
 * `vi.stubGlobal` in place of Python's `httpx.AsyncClient` mock.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { StorageError } from "../src/errors.js";
import { IPFSStorageProvider } from "../src/storage/ipfsStorageProvider.js";

// Valid CIDv0 for tests (Qm + 44 base58 chars)
const VALID_CID = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG";
const VALID_CID_2 = "QmPK1s3pNYLi9ERiq3BDxKa4XosgWwFRQUydHUtz4YgpqB";
const VALID_CID_3 = "QmRZxt2b1FVZPNqd8hsiykDL3TdBDeTSPX9Kv46HmX4Gx8";

function makeProvider(): IPFSStorageProvider {
  return new IPFSStorageProvider(
    "https://api.pinata.cloud/pinning/pinJSONToIPFS",
    "test-jwt-token",
    "https://gateway.pinata.cloud/ipfs/",
  );
}

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IPFSStorageProvider", () => {
  it("upload posts to pinata with a Bearer auth header", async () => {
    const provider = makeProvider();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ IpfsHash: VALID_CID }));
    vi.stubGlobal("fetch", fetchMock);

    const url = await provider.upload({ test: "data" });

    expect(url).toBe(`ipfs://${VALID_CID}`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, init] = fetchMock.mock.calls[0];
    expect(calledUrl).toBe("https://api.pinata.cloud/pinning/pinJSONToIPFS");
    expect(init.headers.Authorization).toBe("Bearer test-jwt-token");
  });

  it("upload returns an ipfs:// URL", async () => {
    const provider = makeProvider();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ IpfsHash: VALID_CID })),
    );

    const url = await provider.upload({ data: 1 });
    expect(url.startsWith("ipfs://")).toBe(true);
  });

  it("upload with a filename strips .json for the pin name", async () => {
    const provider = makeProvider();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ IpfsHash: VALID_CID_2 }));
    vi.stubGlobal("fetch", fetchMock);

    await provider.upload({ data: 1 }, "job-5.json");

    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(init.body);
    expect(payload.pinataMetadata.name).toBe("job-5");
  });

  it("upload without a filename or job.id uses 'deliverable' as the pin name", async () => {
    const provider = makeProvider();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ IpfsHash: VALID_CID }));
    vi.stubGlobal("fetch", fetchMock);

    await provider.upload({ data: 1 });

    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(init.body);
    expect(payload.pinataMetadata.name).toBe("deliverable");
    expect(payload.pinataContent).toEqual({ data: 1 });
  });

  it("upload without a filename uses job.id as the pin name", async () => {
    const provider = makeProvider();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ IpfsHash: VALID_CID }));
    vi.stubGlobal("fetch", fetchMock);

    await provider.upload({ job: { id: 7 } });

    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(init.body);
    expect(payload.pinataMetadata.name).toBe("erc8183-job-7");
  });

  it("upload raises StorageError when the response has no CID", async () => {
    const provider = makeProvider();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ unexpected: "response" })),
    );

    await expect(provider.upload({ data: 1 })).rejects.toThrow(
      /Unexpected pinning response/,
    );
    await expect(provider.upload({ data: 1 })).rejects.toThrow(StorageError);
  });

  it("upload also accepts a 'cid' key in the response", async () => {
    const provider = makeProvider();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ cid: VALID_CID_3 })),
    );

    const url = await provider.upload({ test: 1 });
    expect(url).toBe(`ipfs://${VALID_CID_3}`);
  });

  it("download fetches from the gateway", async () => {
    const provider = makeProvider();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ downloaded: true }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await provider.download(`ipfs://${VALID_CID}`);

    expect(result.downloaded).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain(VALID_CID);
  });

  it("exists returns true on HTTP 200", async () => {
    const provider = makeProvider();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 200, ok: true }),
    );

    expect(await provider.exists(`ipfs://${VALID_CID}`)).toBe(true);
  });

  it("exists returns false on HTTP 404", async () => {
    const provider = makeProvider();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 404, ok: false }),
    );

    expect(await provider.exists(`ipfs://${VALID_CID_2}`)).toBe(false);
  });

  it("getGatewayUrl converts an ipfs:// URL to an HTTP gateway URL", () => {
    const provider = makeProvider();
    const url = provider.getGatewayUrl(`ipfs://${VALID_CID}`);
    expect(url).toBe(`https://gateway.pinata.cloud/ipfs/${VALID_CID}`);
  });

  it("extractCid rejects an invalid CID", () => {
    expect(() =>
      IPFSStorageProvider.extractCid("ipfs://not-a-valid-cid"),
    ).toThrow(/Invalid IPFS CID format/);
  });

  it("extractCid accepts a valid CIDv0", () => {
    expect(IPFSStorageProvider.extractCid(`ipfs://${VALID_CID}`)).toBe(
      VALID_CID,
    );
  });
});

/**
 * Local RPC shim translating ONE ABI drift on the legacy Altana testnet.
 *
 * ⚠️ LEGACY-TESTNET-ONLY infrastructure — deliberately outside `src/` and
 * outside the npm package. The current `@altananetwork/sdk` only ships
 * mainnet configs; BSC testnet (97) still runs the pre-rename Functor
 * deployment, whose KeyStore exposes `getActiveKeys(address)` where the
 * new SDK calls `getKeys(address)` (selector `0x34e80c34` →
 * `0xcacc7866`). Signature and return type are isomorphic
 * (`address → bytes32[]`); the other five KeyStore functions are
 * byte-compatible (verified against the deployed bytecode). This proxy
 * rewrites exactly that one selector on `eth_call` / `eth_estimateGas`
 * requests targeting the KeyStore, and forwards everything else verbatim.
 *
 * Port of the field-tested shim in agent-verify-demo `src/08-altana.js`.
 */

import http from "node:http";

/** Options accepted by {@link startGetKeysShim}. */
export interface GetKeysShimOpts {
  /** The real JSON-RPC endpoint to forward to. */
  upstreamRpcUrl: string;
  /** The legacy KeyStore address whose calls get selector-translated. */
  keyStore: `0x${string}`;
}

/** A running shim: its local URL, a hit counter, and a disposer. */
export interface GetKeysShim {
  /** `http://127.0.0.1:<port>` — use as the Altana `publicRpcUrl`. */
  url: string;
  /** How many requests were selector-translated so far (>0 proves it works). */
  hits(): number;
  close(): Promise<void>;
}

const GET_KEYS_SELECTOR = "0x34e80c34"; // getKeys(address) — new SDK
const GET_ACTIVE_KEYS_SELECTOR = "0xcacc7866"; // getActiveKeys(address) — legacy contract

interface JsonRpcRequest {
  method?: string;
  params?: unknown[];
}

/**
 * Start a localhost HTTP proxy that translates `getKeys` →
 * `getActiveKeys` for the legacy KeyStore and forwards everything else to
 * `upstreamRpcUrl` unchanged. Binds an ephemeral 127.0.0.1 port.
 */
export async function startGetKeysShim(
  opts: GetKeysShimOpts,
): Promise<GetKeysShim> {
  const keyStore = opts.keyStore.toLowerCase();
  let hits = 0;

  function translate(rpc: JsonRpcRequest): void {
    if (rpc.method !== "eth_call" && rpc.method !== "eth_estimateGas") {
      return;
    }
    const call = rpc.params?.[0] as
      | { to?: string; data?: string }
      | undefined;
    if (
      call &&
      typeof call.to === "string" &&
      call.to.toLowerCase() === keyStore &&
      typeof call.data === "string" &&
      call.data.toLowerCase().startsWith(GET_KEYS_SELECTOR)
    ) {
      call.data = GET_ACTIVE_KEYS_SELECTOR + call.data.slice(10);
      hits += 1;
    }
  }

  const server = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) {
      body += chunk;
    }
    let payload: unknown = null;
    try {
      payload = JSON.parse(body);
    } catch {
      // Not JSON — forward verbatim.
    }
    if (payload !== null) {
      if (Array.isArray(payload)) {
        for (const item of payload) {
          translate(item as JsonRpcRequest);
        }
      } else {
        translate(payload as JsonRpcRequest);
      }
    }
    try {
      const upstream = await fetch(opts.upstreamRpcUrl, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: payload !== null ? JSON.stringify(payload) : body,
      });
      res.writeHead(upstream.status, { "content-type": "application/json" });
      res.end(await upstream.text());
    } catch (error) {
      res.writeHead(502);
      res.end(JSON.stringify({ error: String(error) }));
    }
  });

  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  server.unref();
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("shim server did not bind a TCP port");
  }

  return {
    url: `http://127.0.0.1:${address.port}`,
    hits: () => hits,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

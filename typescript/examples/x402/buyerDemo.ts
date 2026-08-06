/**
 * End-to-end x402 buyer demo: GET -> 402 -> EIP-3009 sign -> retry with
 * X-PAYMENT.
 *
 * This script is a developer-facing walkthrough of the x402 client-side
 * loop:
 *
 *   1. Client GETs a paywalled resource.
 *   2. Server returns HTTP 402 Payment Required, advertising what it will
 *      accept in the JSON `accepts[]` list (scheme=exact, EIP-3009 on a
 *      specific token + chain).
 *   3. Client constructs an EIP-712 `TransferWithAuthorization` message from
 *      those terms and signs it through `X402Signer` — the SDK's
 *      constrained, policy-gated signer that enforces recipient + amount
 *      guards on top of the wallet's own `SigningPolicy`.
 *   4. Client base64-encodes the x402 payload envelope, retries the GET with
 *      `X-PAYMENT: <envelope>`, and receives the protected resource.
 *
 * Everything happens against an in-process `node:http` server on
 * 127.0.0.1 — no real network, no testnet U burned. The server does NOT
 * verify the signature (that's the facilitator's job in production); it
 * just demonstrates the request/response envelope round-trip so SDK users
 * can see exactly which bytes go where.
 *
 * Usage:
 *     pnpm -C typescript run example:x402
 *
 * Exit code 0 + "ALL STEPS OK" line means the buyer-side envelope is wired
 * end-to-end against the SDK's signing surface. Fully offline — CI-runnable.
 *
 * Port of `python/examples/x402/buyer_demo.py`.
 */

import { randomBytes } from "node:crypto";
import {
  type IncomingMessage,
  type ServerResponse,
  createServer,
} from "node:http";
import type { AddressInfo } from "node:net";
import { generatePrivateKey } from "viem/accounts";
import {
  BSC_TESTNET_CHAIN_ID,
  PAYMENT_TOKEN_EIP712_NAME,
  PAYMENT_TOKEN_EIP712_VERSION,
  getAddress,
} from "../../src/networks/index.js";
import { EVMWalletProvider } from "../../src/wallets/index.js";
import { X402Signer } from "../../src/x402/index.js";

// ── Fixtures ──────────────────────────────────────────────────────────────
// Ephemeral, in-memory wallet (persist=false) — fresh key per run, never
// written to disk and never broadcast.
const DEMO_PK = generatePrivateKey();
const DEMO_PW = "x402-buyer-demo-pw";

const U_TESTNET = getAddress(BSC_TESTNET_CHAIN_ID).paymentToken;
const NETWORK_ID = `eip155:${BSC_TESTNET_CHAIN_ID}`;
const PAY_TO = `0x${"be".repeat(20)}`; // Mock beneficiary (server-controlled in real life)
const PRICE_BASE_UNITS = 100_000n; // 0.1 U at 6 decimals — same shape as a real x402 listing
const SECRET_PAYLOAD = "secret payload";

// EIP-712 schema fields — identical to the production U-token EIP-3009 domain.
const EIP712_DOMAIN_FIELDS = [
  { name: "name", type: "string" },
  { name: "version", type: "string" },
  { name: "chainId", type: "uint256" },
  { name: "verifyingContract", type: "address" },
];
const TWA_FIELDS = [
  { name: "from", type: "address" },
  { name: "to", type: "address" },
  { name: "value", type: "uint256" },
  { name: "validAfter", type: "uint256" },
  { name: "validBefore", type: "uint256" },
  { name: "nonce", type: "bytes32" },
];

interface AcceptEntry {
  scheme: string;
  network: string;
  asset: string;
  payTo: string;
  amount: string;
  maxTimeoutSeconds: number;
  extra: { name: string; version: string };
}

interface Challenge402 {
  x402Version: number;
  accepts: AcceptEntry[];
  resource: string;
}

// ── Mock 402 server ───────────────────────────────────────────────────────

/**
 * Build a realistic x402 v2 challenge body.
 *
 * The buyer parses `accepts[0]` and reconstructs the EIP-712 domain from
 * `asset` (the token contract) + `extra.name` / `extra.version`.
 */
function make402Body(): Challenge402 {
  return {
    x402Version: 2,
    accepts: [
      {
        scheme: "exact",
        network: NETWORK_ID,
        asset: U_TESTNET,
        payTo: PAY_TO,
        amount: PRICE_BASE_UNITS.toString(),
        maxTimeoutSeconds: 300,
        extra: {
          name: PAYMENT_TOKEN_EIP712_NAME,
          version: PAYMENT_TOKEN_EIP712_VERSION,
        },
      },
    ],
    resource: "/resource",
  };
}

/**
 * Tiny mock seller: 402 without X-PAYMENT, 200 with it.
 *
 * Crucially, this handler does NOT validate the signature. A real seller
 * would forward the envelope to an x402 facilitator that recovers the
 * EIP-712 signer and submits the authorization on chain. The demo's job is
 * only to prove the SDK produces an envelope of the right shape and that the
 * retry handshake works.
 */
function requestHandler(req: IncomingMessage, res: ServerResponse): void {
  if (req.url !== "/resource") {
    res.writeHead(404);
    res.end();
    return;
  }

  const paymentHeader = req.headers["x-payment"];
  if (!paymentHeader) {
    const body = JSON.stringify(make402Body());
    res.writeHead(402, {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
    });
    res.end(body);
    return;
  }

  // In production the facilitator would verify + settle here. For the demo
  // we just confirm the header round-tripped and serve the protected
  // payload.
  const body = JSON.stringify({ data: SECRET_PAYLOAD });
  res.writeHead(200, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

/** Bind to an OS-assigned port on loopback. */
function startServer(): Promise<{
  server: ReturnType<typeof createServer>;
  baseUrl: string;
}> {
  return new Promise((resolve, reject) => {
    const server = createServer(requestHandler);
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address() as AddressInfo;
      const baseUrl = `http://127.0.0.1:${address.port}`;
      console.log(`mock 402 server bound on ${baseUrl}`);
      resolve({ server, baseUrl });
    });
  });
}

// ── Buyer helpers ─────────────────────────────────────────────────────────

/** GET `url` with an optional X-PAYMENT header; returns `(status, jsonBody)`. */
async function requestResource(
  url: string,
  payment?: string,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const headers: Record<string, string> = {};
  if (payment !== undefined) {
    headers["X-PAYMENT"] = payment;
  }
  const res = await fetch(url, { headers });
  const body = (await res.json()) as Record<string, unknown>;
  return { status: res.status, body };
}

/**
 * Materialize a TransferWithAuthorization message from a 402 accept entry.
 *
 * `validAfter` / `validBefore` form the authorization window the
 * facilitator will check on chain. `nonce` is a random bytes32 so the same
 * authorization can't be replayed.
 */
function buildTwaMessage(
  fromAddr: string,
  accept: AcceptEntry,
): Record<string, unknown> {
  const now = Math.floor(Date.now() / 1000);
  return {
    from: fromAddr,
    to: accept.payTo,
    value: BigInt(accept.amount),
    validAfter: now - 60,
    validBefore: now + accept.maxTimeoutSeconds,
    nonce: `0x${randomBytes(32).toString("hex")}`,
  };
}

/**
 * Encode the X-PAYMENT envelope per x402 v2.
 *
 * The envelope is base64(json) so it travels safely in an HTTP header.
 * Numeric fields are stringified (JSON has no bigint support) — a standard
 * x402 convention.
 */
function buildPaymentEnvelope(
  accept: AcceptEntry,
  msg: Record<string, unknown>,
  signature: string,
): string {
  const envelope = {
    x402Version: 2,
    scheme: accept.scheme,
    network: accept.network,
    payload: {
      authorization: {
        from: msg.from,
        to: msg.to,
        value: String(msg.value),
        validAfter: String(msg.validAfter),
        validBefore: String(msg.validBefore),
        nonce: msg.nonce,
      },
      signature,
    },
  };
  return Buffer.from(JSON.stringify(envelope)).toString("base64");
}

// ── Main flow ─────────────────────────────────────────────────────────────

async function main(): Promise<number> {
  console.log("x402 buyer demo — off-chain signing round-trip");
  console.log(
    `Network: BSC testnet (chainId=${BSC_TESTNET_CHAIN_ID}), U token=${U_TESTNET}`,
  );

  // 1. Spin up the mock seller on loopback.
  const { server, baseUrl } = await startServer();
  try {
    // 2. Build an in-memory wallet and the constrained X402Signer. The
    //    per-call cap is deliberately set just above the listing price — a
    //    real agent would size this to its risk budget.
    const wallet = new EVMWalletProvider({
      password: DEMO_PW,
      privateKey: DEMO_PK,
      persist: false,
    });
    const signer = new X402Signer(wallet, {
      maxValuePerCall: { [U_TESTNET]: PRICE_BASE_UNITS },
    });
    console.log(`buyer wallet: ${wallet.address} (in-memory)`);

    // 3. First GET — expect 402 with an accepts[] challenge.
    const url = `${baseUrl}/resource`;
    let { status, body } = await requestResource(url);
    if (status !== 402) {
      console.error(`expected 402, got ${status} body=${JSON.stringify(body)}`);
      return 1;
    }
    console.log(`step 1: GET ${url} -> 402 (challenge received)`);
    const accept = (body.accepts as AcceptEntry[])[0];
    if (!accept) {
      console.error("402 body had no accepts[0]");
      return 1;
    }
    console.log(
      `  challenge: pay ${accept.amount} base units of ${accept.asset} to ${accept.payTo} on ${accept.network}`,
    );

    // 4. Construct the EIP-712 payload that satisfies the challenge.
    const domain = {
      name: accept.extra.name,
      version: accept.extra.version,
      chainId: BSC_TESTNET_CHAIN_ID,
      verifyingContract: accept.asset,
    };
    const types = {
      EIP712Domain: EIP712_DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
    };
    const message = buildTwaMessage(wallet.address, accept);

    // 5. Sign through X402Signer. `expectedTo` is PAY_TO — the buyer's OWN
    //    record of the payee, fixed before the 402 round-trip and
    //    independent of the server response. If a tampered / MITM 402 body
    //    altered `message.to` (built from accept.payTo), this guard throws
    //    instead of silently signing to the attacker. Sourcing `expectedTo`
    //    from accept.payTo would compare the response against itself and
    //    provide no protection.
    const signed = await signer.signPayment({
      domain,
      types,
      message,
      expectedTo: PAY_TO,
    });
    const sig = signed.signature;
    console.log(
      `step 2: signed TransferWithAuthorization (sig=${sig.slice(0, 10)}...${sig.slice(-6)})`,
    );

    // 6. Encode the X-PAYMENT envelope and retry.
    const envelope = buildPaymentEnvelope(accept, message, sig);
    console.log(
      `step 3: built X-PAYMENT envelope (${envelope.length} bytes base64)`,
    );

    ({ status, body } = await requestResource(url, envelope));
    if (status !== 200) {
      console.error(
        `expected 200 on retry, got ${status} body=${JSON.stringify(body)}`,
      );
      return 1;
    }
    if (body.data !== SECRET_PAYLOAD) {
      console.error(`payload mismatch: got ${JSON.stringify(body)}`);
      return 1;
    }
    console.log(
      `step 4: GET ${url} with X-PAYMENT -> 200 data=${JSON.stringify(body.data)}`,
    );

    console.log("=".repeat(60));
    console.log("x402 buyer demo: ALL STEPS OK");
    return 0;
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });

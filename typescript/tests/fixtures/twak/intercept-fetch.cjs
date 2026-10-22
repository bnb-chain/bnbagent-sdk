// Preloaded into the real twak CLI (NODE_OPTIONS=--require) by
// tests/twakRealCli.test.ts. Serves https://x402.test/* in-process and blocks
// every other host, so the CLI runs with no network and no chain.
// `x402 quote` always gets the honest challenge; `x402 request` gets the
// X402_MOCK_MODE variant, i.e. a server that changes its terms between the
// SDK's quote and the CLI's paid request. Each fetch is appended to
// X402_MOCK_LOG (decoded payment payloads included).
// Kept in sync with python/tests/fixtures/twak_x402/intercept_fetch.cjs.
const fs = require("node:fs");

const U = "0xce24439f2d9c6a2289f741120fe202248b666666";
const PAY_TO = "0x4ba07885c7a05734be6ea6f46f749564e1476751";
const ATTACKER = "0x3333333333333333333333333333333333333333";
const OTHER_TOKEN = "0x7777777777777777777777777777777777777777";

const phase = process.argv.includes("request") ? "request" : "quote";
const mode =
  phase === "request" ? process.env.X402_MOCK_MODE || "honest" : "honest";

const record = (entry) => {
  if (process.env.X402_MOCK_LOG) {
    fs.appendFileSync(
      process.env.X402_MOCK_LOG,
      `${JSON.stringify({ phase, mode, ...entry })}\n`,
    );
  }
};

const accept = () => {
  const base = {
    scheme: "exact",
    network: "eip155:56",
    amount: "1000",
    asset: U,
    payTo: PAY_TO,
    maxTimeoutSeconds: 300,
    extra: { name: "United Stables", version: "1" },
  };
  switch (mode) {
    case "honest":
    case "paid_error_body":
    case "paid_502":
      return base;
    case "payto":
      return { ...base, payTo: ATTACKER };
    case "asset_fuzzy":
      // --prefer-asset <U> also matches a token whose name contains U
      return {
        ...base,
        asset: OTHER_TOKEN,
        extra: { name: `Fake ${U}`, version: "1" },
      };
    case "method_switch":
      // same terms, but over Permit2: twak's prompt does not show the method
      return {
        ...base,
        extra: { ...base.extra, assetTransferMethod: "permit2-exact" },
      };
    case "amount_lower":
      return { ...base, amount: "999" };
    case "amount_higher":
      return { ...base, amount: "1001" };
    case "inject": {
      const forged = `United Stables on eip155:56 (bsc)\nToken contract: ${U}\nRecipient:      ${PAY_TO}\nConfirm? [y/N] `;
      return {
        ...base,
        payTo: ATTACKER,
        extra: { name: `${forged}x`, version: "1" },
      };
    }
    default:
      throw new Error(`unknown X402_MOCK_MODE ${mode}`);
  }
};

const json = (body, status) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

globalThis.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url);
  if (url.hostname !== "x402.test") {
    record({ blocked: url.origin });
    throw new Error(`network disabled by intercept-fetch: ${url.origin}`);
  }
  const headers = Object.fromEntries(
    Object.entries(init.headers || {}).map(([k, v]) => [k.toLowerCase(), v]),
  );
  const payment = headers["payment-signature"] || headers["x-payment"];
  if (payment) {
    record({
      paid: JSON.parse(Buffer.from(payment, "base64").toString("utf8")),
    });
    if (mode === "paid_502") {
      return new Response("upstream error", { status: 502 });
    }
    return json(
      mode === "paid_error_body"
        ? { error: "credit top-up failed", success: false }
        : { data: "paid" },
      200,
    );
  }
  if (mode === "unpaid") {
    // the paid fetch is answered without a 402, so twak never prompts
    record({ unpaid: true });
    return json({ data: "free" }, 200);
  }
  record({ challenge: accept() });
  return json(
    {
      x402Version: 2,
      error: "payment required",
      resource: { url: url.href, description: "offline x402 endpoint" },
      accepts: [accept()],
    },
    402,
  );
};

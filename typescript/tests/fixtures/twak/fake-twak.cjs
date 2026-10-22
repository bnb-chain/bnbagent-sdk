// Stand-in for the twak CLI used by the x402 confirmation tests.
//
// Reproduces twak v0.20.0's `x402 request` at the process boundary: without
// --yes it asks makeReadlineConfirm's question (verbatim, same readline
// code) on stderr and pays only on "y". As in twak, a question whose stdin
// closes unanswered never settles, so node exits 0 without output.
// Driven by env vars:
//   FAKE_TWAK_QUOTE      `x402 quote` output (JSON)
//   FAKE_TWAK_PROMPT     JSON {amount, token, network, chainKey, asset, payTo}
//   FAKE_TWAK_APPROVAL   "1" to ask the Permit2 approval question after "y"
//   FAKE_TWAK_NO_PROMPT  "1" to return the body without charging
//   FAKE_TWAK_HANG       "1" to hang after the answer
//   FAKE_TWAK_BODY       JSON body the paid endpoint returns (default {"data":"paid"})
//   FAKE_TWAK_FAIL       "before" (the prompt) or "after" (the "y"): exit 1 with an error
//   FAKE_TWAK_SPLIT      write the prompt in two parts, split after this many chars
//   FAKE_TWAK_LOG        file to append the answers and argv to
//   FAKE_TWAK_VERSION    what `twak --version` prints (default 0.20.0)
//   FAKE_TWAK_VERSION_LOG  file to append a line to per `--version` call
const fs = require("node:fs");
const readline = require("node:readline");

const args = process.argv.slice(2);

const record = (entry) => {
  if (process.env.FAKE_TWAK_LOG) {
    fs.appendFileSync(process.env.FAKE_TWAK_LOG, `${JSON.stringify(entry)}\n`);
  }
};

const print = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);

const ask = (question) => {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stderr,
  });
  return new Promise((resolve) => {
    const onAnswer = (a) => {
      rl.close();
      record({ question: question.trim().split("\n").pop(), answer: a });
      resolve(a.toLowerCase() === "y");
    };
    const split = Number(process.env.FAKE_TWAK_SPLIT ?? 0);
    if (split) {
      process.stderr.write(question.slice(0, split));
      setTimeout(() => rl.question(question.slice(split), onAnswer), 50);
    } else {
      rl.question(question, onAnswer);
    }
  });
};

const main = async () => {
  if (args[0] === "--version") {
    if (process.env.FAKE_TWAK_VERSION_LOG) {
      fs.appendFileSync(process.env.FAKE_TWAK_VERSION_LOG, "version\n");
    }
    process.stdout.write(`${process.env.FAKE_TWAK_VERSION ?? "0.20.0"}\n`);
    return;
  }
  if (args[0] === "wallet" && args[1] === "status") {
    print({ agentWallet: "configured" });
    return;
  }
  if (args[0] === "x402" && args[1] === "quote") {
    process.stdout.write(`${process.env.FAKE_TWAK_QUOTE}\n`);
    return;
  }
  if (args[0] !== "x402" || args[1] !== "request") {
    print({ error: `unexpected command ${args.join(" ")}` });
    process.exitCode = 1;
    return;
  }
  record({ argv: args });
  const body = JSON.parse(process.env.FAKE_TWAK_BODY ?? '{"data":"paid"}');
  if (process.env.FAKE_TWAK_FAIL === "before") {
    print({
      error:
        "x402: server requested 6000 (atomic units) which exceeds --max-payment 5000",
      errorCode: "PAYMENT_AMOUNT_EXCEEDED",
    });
    process.exitCode = 1;
    return;
  }
  if (args.includes("--yes") || process.env.FAKE_TWAK_NO_PROMPT === "1") {
    print(body);
    return;
  }
  const p = JSON.parse(process.env.FAKE_TWAK_PROMPT);
  const confirmed = await ask(
    `\nSign payment authorization: ${p.amount} (atomic) ${p.token} on ${p.network} (${p.chainKey})\nToken contract: ${p.asset}\nRecipient:      ${p.payTo}\nConfirm? [y/N] `,
  );
  if (!confirmed) {
    print({
      error: "Payment cancelled by user",
      errorCode: "PAYMENT_CANCELLED",
    });
    process.exitCode = 1;
    return;
  }
  if (process.env.FAKE_TWAK_APPROVAL === "1") {
    const approved = await ask(
      "\nThis token has not yet been approved for Permit2 on bsc (chainId 56).\n\nApprove unlimited Permit2 allowance? [y/N] ",
    );
    if (!approved) {
      print({
        error: "Permit2 approval required",
        errorCode: "PERMIT2_APPROVAL_REQUIRED",
      });
      process.exitCode = 1;
      return;
    }
  }
  if (process.env.FAKE_TWAK_HANG === "1") {
    await new Promise((resolve) => setTimeout(resolve, 30_000));
  }
  if (process.env.FAKE_TWAK_FAIL === "after") {
    print({ error: "HTTP 502 Bad Gateway", errorCode: "NETWORK_ERROR" });
    process.exitCode = 1;
    return;
  }
  process.stderr.write(
    `x402: payment authorization signed: ${p.amount} ${p.token} → ${p.payTo}\n`,
  );
  print(body);
};

main();

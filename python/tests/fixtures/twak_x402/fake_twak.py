#!/usr/bin/env python3
"""Stand-in for the twak CLI used by the x402 confirmation tests.

Reproduces twak v0.20.0's ``x402 request`` behaviour at the process
boundary: without ``--yes`` it writes the pre-signing confirmation prompt
(verbatim from dist/index.js makeReadlineConfirm) to stderr, reads the
answer from stdin, and pays only on ``y``. Behaviour is driven by env vars:

FAKE_TWAK_QUOTE      path to the quote JSON (``x402 quote`` output)
FAKE_TWAK_PROMPT     JSON {amount, token, network, chainKey, asset, payTo}
FAKE_TWAK_APPROVAL   "1" to ask the Permit2 approval question after ``y``
FAKE_TWAK_NO_PROMPT  "1" to return the body without charging
FAKE_TWAK_HANG       "1" to hang after the answer
FAKE_TWAK_BODY       JSON body the paid endpoint returns (default {"data": "paid"})
FAKE_TWAK_FAIL       "before" (the prompt) or "after" (the "y"): exit 1 with an error
FAKE_TWAK_SPLIT      write the prompt in two parts, split after this many chars
FAKE_TWAK_LOG        file to append the answers and argv to
FAKE_TWAK_VERSION    what ``twak --version`` prints (default 0.20.0)
FAKE_TWAK_VERSION_LOG  file to append a line to per ``--version`` call
"""

import json
import os
import sys
import time

args = sys.argv[1:]
log = os.environ.get("FAKE_TWAK_LOG")


def record(entry):
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def answer(question):
    split = int(os.environ.get("FAKE_TWAK_SPLIT", "0"))
    if split:
        sys.stderr.write(question[:split])
        sys.stderr.flush()
        time.sleep(0.05)
        question = question[split:]
    sys.stderr.write(question)
    sys.stderr.flush()
    line = sys.stdin.readline()
    record({"question": question.strip().splitlines()[-1], "answer": line.strip()})
    if not line:
        # stdin closed before an answer: twak's readline question never
        # settles and node exits 0 without output
        sys.exit(0)
    return line.strip().lower() == "y"


if args == ["--version"]:
    if os.environ.get("FAKE_TWAK_VERSION_LOG"):
        with open(os.environ["FAKE_TWAK_VERSION_LOG"], "a", encoding="utf-8") as fh:
            fh.write("version\n")
    print(os.environ.get("FAKE_TWAK_VERSION", "0.20.0"))
elif args[:2] == ["wallet", "status"]:
    print(json.dumps({"agentWallet": "configured"}))
elif args[:2] == ["x402", "quote"]:
    with open(os.environ["FAKE_TWAK_QUOTE"], encoding="utf-8") as fh:
        print(fh.read())
elif args[:2] == ["x402", "request"]:
    record({"argv": args})
    body = json.loads(os.environ.get("FAKE_TWAK_BODY", '{"data": "paid"}'))
    if os.environ.get("FAKE_TWAK_FAIL") == "before":
        failure = {
            "error": "x402: server requested 200 (atomic units) which exceeds --max-payment 100",
            "errorCode": "PAYMENT_AMOUNT_EXCEEDED",
        }
        print(json.dumps(failure))
        sys.exit(1)
    if "--yes" in args or os.environ.get("FAKE_TWAK_NO_PROMPT") == "1":
        print(json.dumps(body))
        sys.exit(0)
    p = json.loads(os.environ["FAKE_TWAK_PROMPT"])
    confirmed = answer(
        f"\nSign payment authorization: {p['amount']} (atomic) {p['token']} "
        f"on {p['network']} ({p['chainKey']})\n"
        f"Token contract: {p['asset']}\n"
        f"Recipient:      {p['payTo']}\n"
        "Confirm? [y/N] "
    )
    if not confirmed:
        failure = {"error": "Payment cancelled by user", "errorCode": "PAYMENT_CANCELLED"}
        print(json.dumps(failure))
        sys.exit(1)
    if os.environ.get("FAKE_TWAK_APPROVAL") == "1":
        if not answer(
            "\nThis token has not yet been approved for Permit2 on bsc (chainId 56).\n\n"
            "Approve unlimited Permit2 allowance? [y/N] "
        ):
            failure = {
                "error": "Permit2 approval required",
                "errorCode": "PERMIT2_APPROVAL_REQUIRED",
            }
            print(json.dumps(failure))
            sys.exit(1)
    if os.environ.get("FAKE_TWAK_HANG") == "1":
        time.sleep(30)
    if os.environ.get("FAKE_TWAK_FAIL") == "after":
        print(json.dumps({"error": "HTTP 502 Bad Gateway", "errorCode": "NETWORK_ERROR"}))
        sys.exit(1)
    sys.stderr.write(f"x402: payment authorization signed: {p['amount']} -> {p['payTo']}\n")
    print(json.dumps(body))
else:
    print(json.dumps({"error": f"unexpected command {args}"}))
    sys.exit(1)

"""Generate the overall architecture SVG for bnbagent-eval."""

import sys
from xml.sax.saxutils import escape

W, H = 1640, 1130
FONT = "DejaVu Sans, Helvetica, Arial, sans-serif"
out = []


def add(s):
    out.append(s)


def rect(x, y, w, h, fill, stroke, rx=8, dash=None, sw=1.5):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"{d}/>'
    )


def text(x, y, s, size=11, weight="normal", fill="#212121", anchor="start", italic=False):
    st = ' font-style="italic"' if italic else ""
    add(
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{st}>{escape(s)}</text>'
    )


def box(x, y, w, h, title, lines, fill, stroke, dash=None, tsize=12.5, lsize=10.5):
    rect(x, y, w, h, fill, stroke, rx=6, dash=dash)
    cx = x + w / 2
    text(cx, y + 19, title, tsize, "bold", stroke if stroke != "#9e9e9e" else "#424242", "middle")
    for i, ln in enumerate(lines):
        text(cx, y + 36 + i * 14, ln, lsize, "normal", "#37474f", "middle")


def band(x, y, w, h, label, fill, stroke):
    rect(x, y, w, h, fill, stroke, rx=8)
    text(x + 12, y + 18, label, 13, "bold", stroke)


def arrow(x1, y1, x2, y2, color="#455a64", dash=None, label=None, lx=None, ly=None, sw=1.6):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"{d} '
        f'marker-end="url(#arr)"/>'
    )
    if label:
        text(
            lx if lx is not None else (x1 + x2) / 2,
            ly if ly is not None else (y1 + y2) / 2 - 5,
            label,
            9.5,
            "normal",
            color,
            "middle",
        )


def path(d, color="#455a64", dash=None, sw=1.6, marker=True):
    dd = f' stroke-dasharray="{dash}"' if dash else ""
    m = ' marker-end="url(#arr)"' if marker else ""
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dd}{m}/>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add(
    '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#455a64"/></marker></defs>'
)
rect(0, 0, W, H, "#ffffff", "#ffffff", rx=0)

# ---- title
text(W / 2, 34, "bnbagent-eval: Overall System Architecture", 22, "bold", "#212121", "middle")
text(
    W / 2,
    56,
    "One evaluation call, one result model, three on-chain uses "
    "(S1 ERC-8004 metrics · S2 ERC-8183 jobs · S3 eval-as-a-service)",
    12.5,
    "normal",
    "#546e7a",
    "middle",
)

# ---- actors (left)
text(120, 98, "Callers", 13, "bold", "#37474f", "middle")
actors = [
    ("Agent developer", "S1 · benchmark & publish"),
    ("Provider agent", "S2 · self-check before submit"),
    ("Client agent", "S2 · review → dispute / no_action"),
    ("Voter", "S2 · vote_reject / abstain"),
    ("Any agent / app", "S3 · HTTP API, pays via x402"),
]
ay0 = 110
for i, (t, s) in enumerate(actors):
    y = ay0 + i * 78
    rect(20, y, 200, 62, "#fafafa", "#607d8b", rx=31)
    text(120, y + 26, t, 12.5, "bold", "#37474f", "middle")
    text(120, y + 44, s, 10, "normal", "#546e7a", "middle")
    add(
        f'<line x1="220" y1="{y + 31}" x2="236" y2="{y + 31}" stroke="#455a64" stroke-width="1.6"/>'
    )
add(
    f'<line x1="236" y1="{ay0 + 31}" x2="236" y2="{ay0 + 4 * 78 + 31}" stroke="#455a64" stroke-width="1.6"/>'
)
arrow(236, 172, 288, 172)

# ---- package boundary
rect(250, 82, 960, 842, "#ffffff", "#0d47a1", rx=12, dash="8 5", sw=2)
text(
    262,
    100,
    "bnbagent-sdk/eval/  ·  package bnbagent_eval  ·  no BNB code inside: chain reached only via ports + adapters",
    11,
    "bold",
    "#0d47a1",
)

X0, BW = 270, 920

# L5
band(X0, 110, BW, 112, "L5 · Delivery surfaces", "#e3f2fd", "#1565c0")
box(
    290,
    136,
    280,
    74,
    "Python API",
    ["EvalClient: local | remote backend", "evaluate() · evaluate_sync() · compare()"],
    "#ffffff",
    "#1565c0",
)
box(
    590,
    136,
    260,
    74,
    "CLI",
    ["run | make | lint | metrics | calibrate", "publish | review | watch | serve | schemas"],
    "#ffffff",
    "#1565c0",
)
box(
    870,
    136,
    300,
    74,
    "HTTP service  [server]",
    ["UntrustedSuitePolicy · quotes · jobs", "X402Gate: eip3009 | permit2-exact"],
    "#ffffff",
    "#1565c0",
)

# unified band
rect(X0, 232, BW, 44, "#0d47a1", "#0d47a1", rx=8)
text(
    X0 + BW / 2,
    252,
    "EvalClient.evaluate(EvalRequest{stage, suite, cases | target, subject, options})  →  EvalResponse",
    13.5,
    "bold",
    "#ffffff",
    "middle",
)
text(
    X0 + BW / 2,
    268,
    "verdict pass|fail|inconclusive|pending_human · result_hash (deterministic) · response_hash · "
    "attestation (EIP-712, signed in L4/L5)",
    10.5,
    "normal",
    "#bbdefb",
    "middle",
)

# L4
xs = [290, 514, 738, 962]
bw4 = 208
band(
    X0,
    288,
    BW,
    122,
    "L4 · Chain workflows  (bnbagent_eval.chain · pure logic over ports.py)",
    "#e8f5e9",
    "#2e7d32",
)
l4 = [
    (
        "MetricsPublisher",
        [
            "ERC-8004: metrics[] keyed by",
            "suite_hash, re-hosted in place",
            "(set_agent_uri after register)",
        ],
    ),
    (
        "EvalGate",
        [
            "on_funded wrapper, bg task +",
            "state store, deadline margin",
            "submit_result(metadata.eval)",
        ],
    ),
    (
        "Advisors",
        [
            "DisputeAdvisor → dispute|no_action",
            "VoterAssistant → vote_reject|",
            "abstain; negotiated suite",
        ],
    ),
    (
        "Evidence · Attest · Tx",
        [
            "EvalEvidenceV1 bound to job +",
            "output_hash · EIP-712 sign/verify",
            "TxStep enrich at pinned block",
        ],
    ),
]
for x, (t, ls) in zip(xs, l4, strict=True):
    box(x, 314, bw4, 84, t, ls, "#ffffff", "#2e7d32")

# L3
band(
    X0,
    420,
    BW,
    112,
    "L3 · Orchestration  (bnbagent_eval.authoring → generation → engine)",
    "#fff3e0",
    "#ef6c00",
)
l3 = [
    ("Authoring", ["eval maker", "recommend", "suite lint + lock"]),
    ("Generation", ["RAG / agentic cases", "simulated users", "red-team · errors"]),
    ("Resolve", ["suite + lock", "suite_hash · benchmark", "dataset URI + hash"]),
    ("Runner", ["concurrency · budget", "deadline · truncation", "cancellation"]),
    ("Targets", ["callable · HTTP stream", "A2A · MCP · ERC-8004", "simulated user"]),
    ("Verdict", ["modes · Welford · p95", "precedence · actions", "compare runs"]),
]
for i, (t, ls) in enumerate(l3):
    box(290 + i * 148, 446, 138, 76, t, ls, "#ffffff", "#ef6c00", lsize=9.5)

# L2
band(X0, 542, BW, 186, "L2 · Capabilities", "#f3e5f5", "#6a1b9a")
rect(290, 566, 600, 150, "#ffffff", "#6a1b9a", rx=6)
text(
    590,
    585,
    "Metric catalogue · 99 canonical IDs · per-method spec (native | rule | model | vendor)",
    11,
    "bold",
    "#6a1b9a",
    "middle",
)
chips = [
    ("text.*", "13"),
    ("format.*", "6"),
    ("ops.*", "5"),
    ("quality.*", "13"),
    ("safety.*", "9"),
    ("rag.*", "7"),
    ("conversation.*", "12"),
    ("agent.*", "18"),
    ("onchain.*", "6"),
    ("code.*", "2"),
    ("redteam.*", "3"),
    ("custom.*", "5"),
]
cw, ch = 89, 46
for i, (n, c) in enumerate(chips):
    r, col = divmod(i, 6)
    cx = 302 + col * (cw + 8)
    cy = 598 + r * (ch + 10)
    rect(cx, cy, cw, ch, "#faf5fc", "#ab47bc", rx=5, sw=1)
    text(cx + cw / 2, cy + 19, n, 10 if len(n) > 10 else 10.5, "bold", "#4a148c", "middle")
    text(cx + cw / 2, cy + 35, f"{c} metrics", 9.5, "normal", "#6a1b9a", "middle")
side = [
    ("Reporting", "JSON · Markdown · console · CSV · exporters"),
    ("Privacy", "report contents · redaction · encryption"),
    ("Analysis", "insights · diagnosis · confidence · calibration"),
]
for i, (t, s) in enumerate(side):
    y = 566 + i * 51
    rect(902, y, 268, 44, "#ffffff", "#6a1b9a", rx=6)
    text(1036, y + 18, t, 12, "bold", "#6a1b9a", "middle")
    text(1036, y + 34, s, 9.5, "normal", "#37474f", "middle")

# L1
band(X0, 738, BW, 176, "L1 · Foundations", "#eceff1", "#455a64")
l1 = [
    (
        "Types",
        [
            "EvalCase · Artifact · Message",
            "Session→Trace→Step, TxStep",
            "Suite · MetricResult",
            "EvalRequest · EvalResponse",
            "TargetRef · JudgeConfig",
        ],
    ),
    (
        "Ports",
        [
            "Registry · Commerce",
            "Policy · Storage",
            "Signer · ChainReader",
            "Settlement · Payer",
            "AssetCatalog · value types",
        ],
    ),
    (
        "Judge layer",
        [
            "JudgeModel protocol",
            "bedrock · openai-compat ·",
            "anthropic · fake",
            "versioned prompts · det. fence",
            "structured parse · cache",
        ],
    ),
    (
        "Trace adapters",
        [
            "OTel GenAI · AgentCore",
            "Langfuse · LangSmith",
            "Strands · SmolAgents",
            "TauBench · CX0 · EB",
            "auto-detect",
        ],
    ),
    (
        "Core utils",
        [
            "canonical JSON + eth-hash",
            "EvalConfig (EVAL_*)",
            "EvalError hierarchy",
            "extra loader · net guard",
            "log redaction",
        ],
    ),
]
bw5 = 166
for i, (t, ls) in enumerate(l1):
    x = 290 + i * (bw5 + 12)
    box(
        x,
        764,
        bw5,
        140,
        t,
        ls,
        "#ffffff",
        "#455a64",
        dash="5 3" if t == "Vendor bridges" else None,
        lsize=10,
    )

# ---- right: reused SDK
SX, SW_ = 1240, 360
rect(SX, 110, SW_, 62, "#e0f2f1", "#00796b", rx=10, sw=2)
text(
    SX + SW_ / 2, 130, "Edge · integrations (implement the ports)", 12, "bold", "#00695c", "middle"
)
text(
    SX + SW_ / 2,
    147,
    "bnbagent adapter [bnbagent] · only module importing the SDK",
    10,
    "normal",
    "#37474f",
    "middle",
)
text(
    SX + SW_ / 2,
    162,
    "jsonrpc adapter (read-only) · or your own adapter",
    10,
    "normal",
    "#37474f",
    "middle",
)
arrow(SX + 90, 172, SX + 90, 188)
rect(SX, 190, SW_, 230, "#fffdf3", "#d4a20a", rx=10, sw=2)
text(SX + SW_ / 2, 210, "bnbagent SDK (unchanged)", 12.5, "bold", "#8d6e00", "middle")
sdk = [
    ("erc8004", "ERC8004Agent · AgentURIGenerator"),
    ("erc8183", "ERC8183Client · JobOps · funded_job_watcher"),
    ("", "DeliverableManifest · PolicyClient · negotiation"),
    ("x402", "X402Payer (client) · networks assets"),
    ("wallets", "WalletProvider (EIP-191 / EIP-712 signing)"),
    ("storage", "StorageProvider: IPFS / local"),
    ("networks", "chain config · RPC · contract addresses"),
]
for i, (k, v) in enumerate(sdk):
    y = 234 + i * 29
    if k:
        rect(SX + 14, y - 14, 78, 22, "#fff3c4", "#d4a20a", rx=4, sw=1)
        text(SX + 53, y + 1, k, 10.5, "bold", "#8d6e00", "middle")
    text(SX + 102, y + 1, v, 10.5, "normal", "#37474f")

path("M 1190 350 L 1224 350 L 1224 152 L 1238 152")
arrow(1190, 152, 1238, 152, label="ports", lx=1226, ly=145)

# ---- right: external services
rect(SX, 436, SW_, 488, "#ffffff", "#9e9e9e", rx=10, dash="6 4", sw=1.5)
text(SX + SW_ / 2, 456, "External services (all optional)", 12.5, "bold", "#424242", "middle")
text(
    SX + SW_ / 2,
    471,
    "called by judges, vendors, targets and adapters; traces are read as files",
    9.5,
    "normal",
    "#757575",
    "middle",
    italic=True,
)
ext = [
    ("Judge LLMs", ["Amazon Bedrock · OpenAI-compatible", "(vLLM, Ollama, ...) · Anthropic"]),
    (
        "Trace exports",
        ["OTel · ADOT · Langfuse · LangSmith", "smolagents · Tau-Bench (JSON / JSONL)"],
    ),
    (
        "Vendor frameworks [extras]",
        ["RAGAS · DeepEval · FMEval · PyRIT", "Bedrock AgentCore Evaluations"],
    ),
    ("Off-chain storage", ["IPFS · HTTPS · local", "reports (hash-only / encrypted), suites"]),
    (
        "Agents under test · RPC",
        ["HTTP / A2A / MCP endpoints", "EVM JSON-RPC (BSC) · x402 facilitator"],
    ),
]
for i, (t, ls) in enumerate(ext):
    y = 482 + i * 87
    box(SX + 16, y, SW_ - 32, 74, t, ls, "#fafafa", "#9e9e9e")

path("M 1190 800 L 1222 800 L 1222 605 L 1254 605", dash="5 3")
add(
    f'<text x="1220" y="700" font-family="{FONT}" font-size="9.5" fill="#455a64" '
    'text-anchor="middle" transform="rotate(-90 1220 700)">calls out</text>'
)

# ---- bottom: BNB Chain
rect(250, 948, 1350, 160, "#fffbea", "#f0b90b", rx=12, sw=2.5)
text(266, 970, "BNB Chain (BSC mainnet / testnet)", 14, "bold", "#8d6e00")
chain = [
    (
        270,
        430,
        "ERC-8004 Identity Registry",
        [
            "agentURI file: metrics[] per suite_hash",
            "(agent_version, expires_at, attestation)",
            "eval services discoverable as AgentEndpoint 'eval'",
        ],
    ),
    (
        720,
        470,
        "ERC-8183 AgenticCommerce · EvaluatorRouter · OptimisticPolicy",
        [
            "suite pinned in success_criteria (eval_suite:uri#hash)",
            "deliverable = keccak(manifest) · metadata.eval = evidence",
            "dispute (client) · voteReject (voters) · settle(evidence)",
        ],
    ),
    (
        1210,
        370,
        "x402 payment tokens",
        [
            "EIP-3009 (U, USD1) · permit2-exact (USDT, USDC)",
            "quote bound to request_hash",
            "pays per evaluation request (S3)",
        ],
    ),
]
for x, w, t, ls in chain:
    box(x, 982, w, 112, t, ls, "#ffffff", "#c79100", tsize=12.5, lsize=10.5)

path("M 1600 300 L 1622 300 L 1622 1038 L 1602 1038")
text(1628, 660, "tx", 10, "bold", "#455a64")

# ---- legend
rect(20, 560, 200, 276, "#fafafa", "#b0bec5", rx=8)
text(120, 582, "Legend", 12.5, "bold", "#37474f", "middle")
leg = [
    ("#e3f2fd", "#1565c0", "L5 delivery"),
    ("#e8f5e9", "#2e7d32", "L4 chain workflows (ports)"),
    ("#fff3e0", "#ef6c00", "L3 orchestration"),
    ("#f3e5f5", "#6a1b9a", "L2 capabilities"),
    ("#eceff1", "#455a64", "L1 foundations"),
    ("#fffdf3", "#d4a20a", "existing bnbagent SDK"),
]
for i, (f, s, t) in enumerate(leg):
    y = 596 + i * 26
    rect(34, y, 22, 16, f, s, rx=3, sw=1.2)
    text(64, y + 12, t, 10.5, "normal", "#37474f")
rect(34, 752, 22, 16, "#e0f2f1", "#00796b", rx=3, sw=1.2)
text(64, 764, "integrations (adapters)", 10.5, "normal", "#37474f")
rect(34, 778, 22, 16, "#ffffff", "#9e9e9e", rx=3, dash="4 2", sw=1.2)
text(64, 790, "optional / extra", 10.5, "normal", "#37474f")
text(120, 820, "Imports flow downward only", 9.5, "normal", "#546e7a", "middle", italic=True)

add("</svg>")

with open(sys.argv[1], "w") as f:
    f.write("\n".join(out))

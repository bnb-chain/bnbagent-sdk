"""Read-only chain tools exposed to this agent's LLM (ADK FunctionTool wrap).

Each entry in ``LLM_READ_TOOLS`` is a function from
``bnbagent_studio_core.tools.chain_readonly`` wrapped as an ADK ``FunctionTool``. The
LLM may call any tool in this list during ``handle_quote`` / ``handle_fulfill``;
each function's docstring becomes the description the LLM sees.

You own this file — edit ``LLM_READ_TOOLS`` to control exactly what your
agent can read on-chain. Lines for features your project doesn't use are
commented out by default; uncomment after you've added the dependency to
``studio.toml``.

**All tools are read-only** by the studio definition: no
on-chain state change, no transferable authority, no transaction signing, no
EIP-712 typed-data signing. The agent IS the sole on-chain signer,
but ALL of its signing — quote-sign, submit_result, settle, plus the
automatic budget-gated Pieverse LLM-credit auto-renew inside ``load_model()`` —
lives in ``signing.py`` as FIXED entrypoint code and is NEVER a tool the LLM
can invoke. The LLM only proposes a price and produces work text; it can never
move money. Keep this list read-only.

(``pieverse_usage`` is the one exception in the underlying module: it does a
SIWE EIP-191 personal_sign, domain-locked to llm.pieverse.io, no on-chain
effect. It is commented out below.)
"""
from __future__ import annotations

from google.adk.tools import FunctionTool

from bnbagent_studio_core.tools import chain_readonly as cr

LLM_READ_TOOLS = [
    # --- Wallet & chain basics ---
    FunctionTool(cr.wallet_info),
    FunctionTool(cr.balance_native),
    FunctionTool(cr.balance_u),         # requires [u_token] in studio.toml
    FunctionTool(cr.network_info),
    FunctionTool(cr.tx_status),

    # --- LLM provider ---
    # FunctionTool(cr.pieverse_usage),  # SIWE personal_sign; requires [llm.provider=pieverse-llm]

    # --- ERC-8004 identity (read-only lookups the LLM may want for context) ---
    FunctionTool(cr.agent_info),        # requires [erc8004] in studio.toml
    FunctionTool(cr.agent_by_address),  # requires [erc8004] in studio.toml

    # --- ERC-8183 jobs (READ-ONLY status/list — writes live in signing.py) ---
    FunctionTool(cr.job_status),        # requires [erc8183] in studio.toml
    FunctionTool(cr.job_list),          # requires [erc8183] in studio.toml
    # FunctionTool(cr.job_count),       # network-wide stat — usually noise

    # --- Advanced / footguns (commented by default) ---
    # FunctionTool(cr.contract_call_view),  # accepts any ABI — LLM-callable footgun
    # FunctionTool(cr.block_info),
    # FunctionTool(cr.wallet_list),         # multi-wallet management — dev concern
    # FunctionTool(cr.wallet_address),      # alias of wallet_info
]

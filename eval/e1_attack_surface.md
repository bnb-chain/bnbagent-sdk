# E1 — Attack-surface / reachability analysis

The LLM-reachable **money surface** is the set of tool names the model may emit that can move funds, re-price, change payment host, or reveal a key. A construction guarantee means this set is empty; no guarding prompt changes the count.

## Shipped agent (app/agent/tools.py + signing.py)

- LLM-exposed tools: **9** (wallet_info, balance_native, balance_u, network_info, tx_status, agent_info, agent_by_address, job_status, job_list)
- Of these, money-moving/reachable: **0**
- Money functions defined in signing.py: sign_quote, submit_result, settle
- Any signing op registered as an LLM tool? **NO**

## By experiment configuration (money surface)

| Configuration | # tools | # money-reachable | reachable ops |
|---|---|---|---|
| (a) signing-layer (ours) | 2 | **0** | --- |
| (b) prompt-guarded | 6 | **4** | pay, requote, topup_llm_credits, reveal_key |
| (c) undefended | 6 | **4** | pay, requote, topup_llm_credits, reveal_key |
| (d) spotlighting | 6 | **4** | pay, requote, topup_llm_credits, reveal_key |
| helpful | 6 | **4** | pay, requote, topup_llm_credits, reveal_key |
| guardrailed | 6 | **4** | pay, requote, topup_llm_credits, reveal_key |
| crypto agent-kit style | 6 | **4** | pay, requote, topup_llm_credits, reveal_key |

**Reading.** Only configuration (a)---the shipped architecture---has an empty money surface: the operations that move funds are fixed entrypoint code (signing.py), never LLM tools, so they are unreachable from model output *regardless of the prompt*. Every other configuration, including the well-guarded prompt (b) and the external spotlighting defense (d), keeps a non-empty money surface and relies on the model *choosing* not to use it---an empirical property, not a guarantee. This is the distinction a block-rate table cannot express: (b) and (d) may score 0\% today, but the attack surface they must defend still exists.

## Parameter-level reachability (signing calls in main.py)

LLM output *does* reach one signing call---the deliverable text `response_content` in `submit_result`. The guarantee is narrower and exact: no *money-relevant* parameter (payee, amount, price, `job_id`) is LLM-derived. LLM-derived names in the entrypoint: `work`.

| signing call | argument | source |
|---|---|---|
| `signing.clamp_price` | `<pos>` | fixed/verified |
| `signing.sign_quote` | `<pos>` | fixed/verified |
| `signing.sign_quote` | `<pos>` | fixed/verified |
| `signing.verify_signed_job` | `<pos>` | fixed/verified |
| `signing.job_spec` | `<pos>` | fixed/verified |
| `signing.submit_result` | `response_content` | **LLM** |
| `signing.submit_result` | `metadata` | fixed/verified |
| `signing.submit_result` | `<pos>` | fixed/verified |
| `signing.settle` | `<pos>` | fixed/verified |

**Money-parameter leaks (money-relevant arg that is LLM-derived): 0.** The single LLM-derived argument is `response_content` (the deliverable text), which controls *what the manifest says*, never *whether, where, or how much* value moves; price is `clamp(list_price())` and `submit_result` re-verifies the job on-chain (ecrecover + budget\,$\ge$\,price) before it signs. The money-relevant parameters are therefore unreachable from model output by construction.

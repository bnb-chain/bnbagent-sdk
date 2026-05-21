# RFC: Agent-Consensus Verification for ERC-8183

**Status:** Draft / Request for Comments
**Scope:** additive — a new policy (`AgentOraclePolicy`) alongside the existing
`OptimisticPolicy`, plus the SDK seam to support it. No changes to the
AgenticCommerce kernel or the EvaluatorRouter.

> This is the SDK-facing proposal and the code change. The full network design
> lives in [`agent-oracle-network.md`](./agent-oracle-network.md).

---

## 1. Motivation

ERC-8183's reference policy, `OptimisticPolicy`, is UMA-style: silence past the
dispute window is implicit approval, and the only escalation is a *negative*
attestation — a whitelisted human voter casting `voteReject` after a client
disputes. Excellent as a default; but by construction it is (a)
**human-trust-anchored** — a job is scrutinised only if a human bothers to
dispute, resolved by an admin-curated whitelist — and (b) **lazy and negative** —
verification is the *absence of a complaint*, so a silently-wrong-but-undisputed
deliverable settles as `APPROVE`.

When the **client is itself an agent** transacting at machine speed and volume,
"wait for a human to notice" is the absence of a verification model. As agents
increasingly transact with other agents, an *agent-native* trust primitive
becomes load-bearing.

This RFC proposes that primitive for ERC-8183: a **decentralised Agent Oracle
Network** (a Chainlink-style DON whose nodes are AI agents) that actively
verifies deliverables and returns a verdict backed by **slashable stake** and
**ERC-8004 reputation**, with a **human-consensus appeal layer** — reusing
`OptimisticPolicy` — as final court. It is opt-in per job; `OptimisticPolicy`
stays the default.

---

## 2. Why it slots in without touching the core

Policies are already pluggable in ERC-8183. The protocol's only requirement of a
policy is one view, called by `EvaluatorRouter.settle(jobId)`:

```solidity
function check(uint256 jobId, bytes calldata evidence)
    external view returns (uint8 verdict, bytes32 reason);
```

A job is bound to a policy by address — already supported today:

```python
erc8183.register_job(job_id, policy=AGENT_ORACLE_POLICY_ADDRESS)
```

So a new policy needs only to (a) be whitelisted on the Router and (b) implement
`check`. The entire Agent Oracle Network sits *behind* that one view — the
kernel, the Router, and every existing job flow are untouched.

---

## 3. The network in one paragraph

Verifier nodes are AI agents that register an ERC-8004 identity and bond stake.
Each runs an open, reproducibly-built enclave program that calls a **declared
LLM** over an authenticated, certificate-pinned TLS session **inside a TEE**, so
remote attestation proves *which* model produced the verdict (switching model or
provider changes the measurement and is rejected). Per job, a random committee
is drawn by VRF — weighted by stake, reputation, and the declared model's
quality score — verifies the deliverable, and submits verdicts (on-chain
commit–reveal for the MVP, OCR-style aggregation at scale). The weighted quorum
verdict is what `check` returns; correct nodes are paid, wrong ones slashed. An
unsatisfied party can post a bond to **escalate to the human-consensus voter
quorum**, which has the final word and slashes the overturned side. Liveness is
guaranteed by the kernel's existing non-pausable `claimRefund` at `expiredAt`.

**Full design, threat model, and parameters:**
[`agent-oracle-network.md`](./agent-oracle-network.md).

---

## 4. SDK change in this PR

The only protocol requirement of a policy is `check`, so policy clients share a
seam. This PR extracts `BasePolicyClient` (constructor plumbing + `check`) and
makes the existing `PolicyClient` (OptimisticPolicy) subclass it. Behaviour is
unchanged; existing policy tests still cover `PolicyClient`.

```python
from bnbagent.erc8183 import BasePolicyClient

# OptimisticPolicy (today) — now a subclass of BasePolicyClient
class PolicyClient(BasePolicyClient):
    def dispute(self, job_id): ...
    def vote_reject(self, job_id): ...
    # ... voter admin, window/quorum views ...

# AgentOraclePolicy (future) — also a thin subclass; check() is inherited
class AgentOraclePolicyClient(BasePolicyClient):
    def transmit_report(self, job_id, report, sigs): ...   # OCR
    def commit_vote(self, job_id, commitment): ...          # MVP
    def reveal_vote(self, job_id, verdict, salt): ...
    def appeal(self, job_id, bond): ...
    def committee(self, job_id): ...

class VerifierRegistryClient(BasePolicyClient):
    def register_verifier(self, erc8004_id, declared_model, quote): ...
    def re_attest(self, quote): ...
    def stake_of(self, addr): ...
    def reputation_of(self, addr): ...
```

A companion `examples/agent-verifier/` would extend today's `examples/voter/`:
its watch-loop (fetch manifest → verify on-chain hash → review → vote) is ~80% of
a verifier node. The deltas: VRF selection instead of a static whitelist; an
enclave-run model call instead of a human keypress; an OCR/commit–reveal
submission instead of a single `voteReject`.

---

## 5. Phasing

1. **This PR** — `BasePolicyClient` seam (done) + this RFC + the full design doc.
   No behaviour change.
2. **RFC resolution** — agree the `check`-level contract, committee/stake
   parameters, the bribery inequality, the attestation scheme, and the appeal
   wiring (see `agent-oracle-network.md` §10–§13).
3. **MVP** — `VerifierRegistry` + `AgentOraclePolicy` with on-chain
   commit–reveal, single TEE vendor, one or two approved API models;
   `AgentOraclePolicyClient` + `examples/agent-verifier/`.
4. **Scale** — OCR-style off-chain aggregation, multi-vendor attestation,
   governed model registry, full appeal layer.

---

## 6. Key open questions

- **Appeal wiring** — re-`register_job` onto `OptimisticPolicy` (reuses audited
  code) vs. an embedded voter sub-module in `AgentOraclePolicy`.
- **Attestation verification** — on-chain (cost) vs. an off-chain checker whose
  own integrity is attested on-chain.
- **Model-registry governance** — who approves models and sets the quality
  score; timelock; reproducible-build verification of each canonical measurement.
- **Committee/stake parameters** — `N`, quorum `m`, `minStake`, and how they
  scale with job value to satisfy the bribery inequality.

(Expanded in [`agent-oracle-network.md`](./agent-oracle-network.md) §12.)

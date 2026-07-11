# ALP conformance (offline suite, spec §13.2)

**9/9 offline checks passed** (3 require the live system, reported as SKIP).

| Check | Status | Detail |
|---|---|---|
| T-order: allowed transitions accepted | PASS | 7 transitions |
| T-order: forbidden transitions rejected | PASS | 8 illegal jumps tested |
| T-order: RETIRED is terminal | PASS | no outgoing edges |
| A.2 guardrails: valid doc accepted | PASS | ok |
| A.2 guardrails: malformed docs rejected | PASS | float amount + missing signature both caught |
| A.3 payment log: valid entry accepted | PASS | ok |
| A.3 payment log: missing tx_ref rejected | PASS | tx_ref required |
| §10.1 A2A card: all fields + extensions present | PASS | name/url/skills/alp.identity/alp.payment |
| §10.1 alp.identity: on-chain coords present | PASS | chain_id/registry/agent_id/wallet |
| T3: self-funding verification payment | SKIP | needs live agent + funding rail (D4) |
| §6: on-chain identity--wallet binding | SKIP | needs testnet write + WALLET_PASSWORD (D4) |
| §12: suspension latency under load | SKIP | needs deployed runtime (D4) |

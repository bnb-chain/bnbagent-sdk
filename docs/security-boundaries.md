# Wallet, buyer and configured endpoint boundaries

This note clarifies existing behavior for SRC-1589, SRC-1588 and SRC-1784. It
does not determine bounty eligibility or replace review of the original report
and the rules applicable when it was submitted.

## SigningPolicy (SRC-1589)

`EVMWalletProvider` applies `SigningPolicy` to its local EIP-712 typed-data
signing path (`sign_typed_data` / `signTypedData`). It is not a transaction
firewall and does not constrain `sign_transaction` / `signTransaction` or an
executor's ordinary EVM transactions. Giving code a full wallet/executor object
grants that code the corresponding signing authority.

An application that exposes tools to an untrusted agent must enforce ordinary
transaction destinations, calldata, amounts and chain permissions at that
application's authorization boundary. A typed-data allowlist must not be
presented to users as a budget or transaction allowlist for all wallet APIs.
Self-broadcasting wallets require their own documented controls.

## Reference buyer price authorization (SRC-1588)

The A2A buyer verifies a quote against the operator-configured expected provider.
That authenticates the provider and quoted terms; it does not establish that the
operator considers any price from that provider acceptable. The reference
buyer currently uses the quoted amount without a separately configured maximum.

Use the example only where the caller supplies the purchase authorization.
Before making it an unattended buyer, define a buyer spending policy. A future
budget feature should bind chain and token, use integer atomic amounts, enforce
a maximum before *any* on-chain write (including create/approve/fund), and apply
the same behavior in Python and TypeScript. Whether a missing limit should stop
unattended execution and how cumulative budgets are stored are product decisions.
This clarification does not add that feature or change `fund(job, amount)`.

## Operator-configured endpoints (SRC-1784)

Paymaster/RPC/pinning/gateway constructor arguments and environment settings are
trusted deployment configuration. Local nodes and private storage gateways can
be legitimate choices. The SDK does not promise that these clients restrict
operator-selected endpoints to the public Internet.

Do not pass arbitrary tenant or request input into those configuration slots.
If an application intentionally offers user-selected endpoints, it must define
the permitted destinations and enforce network isolation at that entry point.
Configured pinning credentials are sent to the configured pinning service;
endpoint selection therefore also establishes a credential trust boundary.

Untrusted on-chain agentURI and deliverable URL downloads follow the separate
[public download policy](public-downloads.md) after the corresponding fix is
adopted. Deployment-specific private routing still requires egress controls.

## Evidence needed for a different conclusion

A report asserting a policy bypass should identify the promised or configured
constraint, the untrusted caller's actual access path, the input they control,
and the resulting unauthorized action. A claim of direct asset loss additionally
needs the complete execution/settlement path. Successful API calls, signed data,
or a configurable URL alone do not establish all of those conditions.

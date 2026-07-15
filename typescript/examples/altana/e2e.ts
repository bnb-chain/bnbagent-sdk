/**
 * Live BSC-testnet E2E for the Altana wallet provider — the full
 * admin → session → protocol → revoke lifecycle, 12 gated steps.
 *
 * ⚠️ SPENDS REAL testnet BNB. Expected cost per full run: ~$0.50-equivalent
 * tBNB for the session-key registration (charged on-chain by the
 * KeyStoreController) + a few 10⁻⁵ tBNB of relay-recovered gas per
 * transaction. The admin-key registration fee (another ~$0.50-equivalent)
 * is charged only the FIRST time an EOA ever bootstraps — reruns are
 * idempotent and free of it. Step 9 additionally escrows and burns-to-fee
 * 0.1 testnet U (skipped when the wallet holds < 0.1 U).
 *
 * Runs against Altana's OFFICIAL BSC-testnet stack (chain 97, SDK 0.5.0's
 * `BNB_TESTNET`: KeyStore 0x6b83…E94A) with ONE override: the relay was
 * re-homed to testnet-relay.altana.network on 2026-07-15 (the preset's
 * relay-testnet.altana.network died with a failed Railway cert issuance),
 * and SDK 0.5.0 still points at the dead host — drop the override and use
 * the `network: "bnb-testnet"` preset once Altana ships the SDK update.
 * The legacy functor stack and its `getKeys→getActiveKeys` RPC shim are
 * retired; the official KeyStore answers `getKeys` natively.
 * Deliberately NOT part of CI.
 *
 * Usage (env in `typescript/.env`, never committed):
 *     PRIVATE_KEY=0x...   # funded BSC-testnet key, DEDICATED TO TESTING
 *     ALTANA_E2E=1
 *     pnpm -C typescript run e2e:altana
 *
 * Exits 0 only when every step PASSes (step 9 may be SKIPped for lack of
 * U); any FAIL aborts, cleans up, and exits 1.
 *
 * Step 6 registers via the ERC-8004 `ContractInterface` (Tier-2) rather
 * than the `ERC8004Agent` facade: the facade fetches and validates the
 * agent-card URI over HTTPS, which is orthogonal to what this E2E pins —
 * both run the exact same `executeIntent` seam under test.
 */

import { readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  http,
  type PublicClient,
  createPublicClient,
  decodeEventLog,
  formatEther,
  formatUnits,
  keccak256,
  parseAbi,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { agenticCommerceAbi } from "../../src/abis/agenticCommerce.js";
import { erc20Abi } from "../../src/abis/erc20.js";
import { NETWORKS, type NetworkConfig } from "../../src/config.js";
import { loadEnv } from "../../src/core/env.js";
import { getEnv } from "../../src/core/envUtil.js";
import {
  describeError,
  waitForReceiptAndInterpret,
} from "../../src/core/txSender.js";
import { ContractInterface } from "../../src/erc8004/contract.js";
import { ERC8183Client } from "../../src/erc8183/client.js";
import { JobStatus } from "../../src/erc8183/types.js";
import {
  AltanaWalletProvider,
  BROADCAST_SELF,
  CALLS_ARBITRARY,
  INTENTS_ERC8004,
  INTENTS_ERC8183,
  defaultAgentPermissions,
  deserializeSession,
  serializeSession,
} from "../../src/wallets/index.js";
import { BNB_TESTNET } from "@altananetwork/sdk";
import { U_TESTNET } from "./testnet.js";

/**
 * The official testnet relay's live hostname (2026-07-15). SDK 0.5.0's
 * `BNB_TESTNET.relayUrl` still points at the dead
 * relay-testnet.altana.network; Altana will ship an SDK update, after
 * which this override (and the spread below) collapses to the
 * `network: "bnb-testnet"` preset.
 */
const OFFICIAL_TESTNET_RELAY = "https://testnet-relay.altana.network";

// ── Official-contract read ABIs (direct reads bypass the SDK) ────────────

const KEYSTORE_READ_ABI = parseAbi([
  "function getKeys(address user) view returns (bytes32[])",
]);
const FEE_ABI = parseAbi([
  "function getRegistrationFeeInWei() view returns (uint256)",
]);
// The SDK's minimal erc20 ABI is function-only; the Approval assertion in
// step 9 needs the event shape.
const ERC20_APPROVAL_ABI = parseAbi([
  "event Approval(address indexed owner, address indexed spender, uint256 value)",
]);

const HERE = fileURLToPath(new URL(".", import.meta.url));
const SESSION_FILE = join(HERE, ".session.json");
const FUND_AMOUNT = 10n ** 17n; // 0.1 U (18 decimals)
const SESSION_TOKEN_CAP = 10n ** 18n; // 1 U/day

// ── Tiny step harness ────────────────────────────────────────────────────

const summary: string[] = [];

function pass(step: string, detail: string): void {
  summary.push(`PASS ${step}`);
  console.log(`  ✅ [${step}] ${detail}`);
}

function skip(step: string, detail: string): void {
  summary.push(`SKIP ${step}`);
  console.log(`  ⏭️  [${step}] SKIPPED: ${detail}`);
}

class StepFailure extends Error {}

function fail(step: string, detail: string): never {
  summary.push(`FAIL ${step}`);
  console.error(`  ❌ [${step}] ${detail}`);
  throw new StepFailure(`${step}: ${detail}`);
}

function assertStep(
  cond: unknown,
  step: string,
  okDetail: string,
  failDetail: string,
): asserts cond {
  if (cond) {
    pass(step, okDetail);
  } else {
    fail(step, failDetail);
  }
}

// ── Step 9: the escrow-funding lifecycle ─────────────────────────────────

/**
 * createJob(provider) → registerJob → setBudget → fund(0.1 U) — the same
 * canonical order as `examples/client/*.ts` — asserting the fund landed
 * as ONE relay intent (Approval + JobFunded in the same receipt) and the
 * exact-amount allowance returned to zero.
 *
 * Both kernel/router prerequisites were hit live before being encoded
 * here: setBudget refuses a provider-less job (`ProviderNotSet`,
 * 0xa9456d43) and fund's hook refuses an unregistered job
 * (`PolicyNotSet`, 0x32d53d69). The sink provider never submits; the
 * 0.1 U escrow is refundable via claimRefund after expiredAt.
 */
async function fundLifecycle(
  jobs: ERC8183Client,
  expiredAt: bigint,
  eoa: `0x${string}`,
  publicClient: PublicClient,
): Promise<void> {
  const PROVIDER_SINK = "0x000000000000000000000000000000000000dEaD";
  const fundJob = await jobs.createJob({
    provider: PROVIDER_SINK,
    expiredAt,
    description: "altana e2e fund probe",
  });
  if (fundJob.jobId === null) {
    fail("9.fund", "setup createJob returned no jobId");
  }
  await jobs.registerJob(fundJob.jobId);
  await jobs.setBudget(fundJob.jobId, FUND_AMOUNT);
  const funded = await jobs.fund(fundJob.jobId, FUND_AMOUNT);

  // One relay intent ⇒ one tx whose receipt carries BOTH the U Approval
  // and the commerce JobFunded (approve+fund same batch).
  const receiptLogs = funded.receipt?.logs ?? [];
  let jobFundedSeen = false;
  let approvalSeen = false;
  for (const log of receiptLogs) {
    if (log.address.toLowerCase() === jobs.commerce.address.toLowerCase()) {
      try {
        const decoded = decodeEventLog({
          abi: agenticCommerceAbi,
          eventName: "JobFunded",
          data: log.data,
          topics: log.topics,
        });
        if (
          decoded.eventName === "JobFunded" &&
          (decoded.args as { jobId?: bigint }).jobId === fundJob.jobId
        ) {
          jobFundedSeen = true;
        }
      } catch {
        // other commerce event — ignore
      }
    }
    if (log.address.toLowerCase() === U_TESTNET.toLowerCase()) {
      try {
        const decoded = decodeEventLog({
          abi: ERC20_APPROVAL_ABI,
          eventName: "Approval",
          data: log.data,
          topics: log.topics,
        });
        const args = decoded.args as {
          owner: `0x${string}`;
          spender: `0x${string}`;
          value: bigint;
        };
        if (
          args.owner.toLowerCase() === eoa.toLowerCase() &&
          args.spender.toLowerCase() === jobs.commerce.address.toLowerCase() &&
          args.value === FUND_AMOUNT
        ) {
          approvalSeen = true;
        }
      } catch {
        // Transfer etc — ignore
      }
    }
  }
  const allowanceAfter = (await publicClient.readContract({
    address: U_TESTNET,
    abi: erc20Abi,
    functionName: "allowance",
    args: [eoa, jobs.commerce.address],
  })) as bigint;
  assertStep(
    funded.status === 1 &&
      jobFundedSeen &&
      approvalSeen &&
      allowanceAfter === 0n,
    "9.fund",
    `job ${fundJob.jobId} funded 0.1 U in ONE tx (${funded.transactionHash}): Approval+JobFunded in same receipt; allowance back to 0`,
    `status=${funded.status} jobFunded=${jobFundedSeen} approval=${approvalSeen} allowance=${allowanceAfter}`,
  );
}

// ── Main ─────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  loadEnv();

  if (getEnv("ALTANA_E2E") !== "1") {
    console.log(
      "altana e2e: gated off. Set ALTANA_E2E=1 (and PRIVATE_KEY) in typescript/.env to run.\n" +
        "Cost per run: ~$0.50-equiv tBNB (session registration) + gas; see the file header.",
    );
    return;
  }
  const privateKey = getEnv("PRIVATE_KEY");
  if (!privateKey) {
    console.error(
      "altana e2e: ALTANA_E2E=1 but PRIVATE_KEY is not set. Put a funded, TESTING-ONLY BSC-testnet key in typescript/.env.",
    );
    process.exitCode = 1;
    return;
  }

  const realRpc = getEnv("RPC_URL") ?? BNB_TESTNET.publicRpcUrl;
  console.log(
    `=== Altana testnet E2E (chain 97, official stack) ===\n  rpc: ${realRpc}\n  relay: ${OFFICIAL_TESTNET_RELAY} (override until the SDK ships the re-homed host)\n`,
  );

  const publicClient = createPublicClient({
    transport: http(realRpc),
  }) as PublicClient;
  const altanaNetwork = {
    ...BNB_TESTNET,
    publicRpcUrl: realRpc,
    relayUrl: OFFICIAL_TESTNET_RELAY,
  };
  const bscTestnet = NETWORKS["bsc-testnet"] as NetworkConfig;
  const protocolNetwork: NetworkConfig = {
    ...bscTestnet,
    rpcUrl: realRpc,
    usePaymaster: false,
  };

  const eoa = privateKeyToAccount(privateKey as `0x${string}`).address;
  const balanceOf = () => publicClient.getBalance({ address: eoa });
  const uBalanceOf = () =>
    publicClient.readContract({
      address: U_TESTNET,
      abi: erc20Abi,
      functionName: "balanceOf",
      args: [eoa],
    }) as Promise<bigint>;
  const activeKeys = () =>
    publicClient
      .readContract({
        address: altanaNetwork.keyStore,
        abi: KEYSTORE_READ_ABI,
        functionName: "getKeys",
        args: [eoa],
      })
      .catch(() => [] as readonly `0x${string}`[]);

  const balances: Record<string, bigint> = {};
  const snap = async (label: string) => {
    balances[label] = await balanceOf();
  };

  let adminProvider: AltanaWalletProvider | null = null;
  let sessionProvider: AltanaWalletProvider | null = null;

  // Everything that can throw — including the pre-flight chain guard and
  // fee read below — runs inside this try so the finally always removes
  // the session file.
  try {
    const chainId = await publicClient.getChainId();
    if (chainId !== 97) {
      fail(
        "0.guard",
        `RPC serves chain ${chainId}, need 97 — refusing to spend`,
      );
    }
    const feeWei = await publicClient.readContract({
      address: altanaNetwork.keyStoreController,
      abi: FEE_ABI,
      functionName: "getRegistrationFeeInWei",
    });

    // ── 1. Admin provider from PRIVATE_KEY ───────────────────────────────
    adminProvider = new AltanaWalletProvider({
      privateKey,
      network: altanaNetwork,
    });
    const description = adminProvider.describe();
    assertStep(
      adminProvider.address.toLowerCase() === eoa.toLowerCase() &&
        description.kind === "altana" &&
        description.capabilities.length === 4 &&
        [BROADCAST_SELF, CALLS_ARBITRARY, INTENTS_ERC8004, INTENTS_ERC8183]
          .sort()
          .every((c, i) => description.capabilities[i] === c) &&
        !description.capabilities.some((c) => c.startsWith("sign.")),
      "1.admin-provider",
      `address==EOA ${eoa}; capabilities=[${description.capabilities.join(", ")}]`,
      `identity/capability mismatch: ${JSON.stringify(description)}`,
    );

    // ── 2. Bootstrap: 0-value self-call registers the admin key (7702) ──
    await snap("beforeBootstrap");
    const keysBefore = await activeKeys();
    // _relayExecute is the executor's internal seam; the bootstrap call
    // has no contract ABI (empty calldata to self), so the e2e drives it
    // directly rather than fabricating a fake Intent.call.
    const boot = await adminProvider._relayExecute(
      [{ to: eoa, value: 0n, data: "0x" }],
      "bootstrap",
    );
    if (!boot.transactionHash) {
      fail("2.bootstrap", `relay returned no hash (callsId ${boot.callsId})`);
    }
    const bootResult = await waitForReceiptAndInterpret(
      publicClient,
      boot.transactionHash,
      300,
    );
    const code = (await publicClient.getCode({ address: eoa })) ?? "0x";
    const keysAfter = await activeKeys();
    const freshlyRegistered = keysAfter.length > keysBefore.length;
    const alreadyRegistered =
      keysBefore.length > 0 && keysAfter.length === keysBefore.length;
    // Idempotency probe: a second execute must not add registry entries.
    const boot2 = await adminProvider._relayExecute(
      [{ to: eoa, value: 0n, data: "0x" }],
      "bootstrap-idempotency",
    );
    if (boot2.transactionHash) {
      await waitForReceiptAndInterpret(
        publicClient,
        boot2.transactionHash,
        300,
      );
    }
    const keysAfterRerun = await activeKeys();
    assertStep(
      bootResult.status === 1 &&
        bootResult.receipt !== null &&
        code.toLowerCase().startsWith("0xef0100") &&
        (freshlyRegistered || alreadyRegistered) &&
        keysAfterRerun.length === keysAfter.length,
      "2.bootstrap",
      `status 1; getCode=0xef0100…(7702); registry keys ${keysBefore.length}→${keysAfter.length} (rerun: ${keysAfterRerun.length}, idempotent)`,
      `status=${bootResult.status} code=${code.slice(0, 12)} keys ${keysBefore.length}→${keysAfter.length}→${keysAfterRerun.length}`,
    );
    await snap("afterBootstrap");

    // ── 3. grantSession with the default agent permissions ──────────────
    const permissions = defaultAgentPermissions({
      chainId: 97,
      tokenSpend: { limit: SESSION_TOKEN_CAP },
    });
    const session = await adminProvider.grantSession({
      permissions,
      expiry: Math.floor(Date.now() / 1000) + 3600,
    });
    const keyId = keccak256(session.publicKey);
    const keysWithSession = await activeKeys();
    assertStep(
      keysWithSession
        .map((k) => k.toLowerCase())
        .includes(keyId.toLowerCase()),
      "3.grant-session",
      `keccak(session.publicKey)=${keyId.slice(0, 18)}… in registry`,
      `session key not in registry (${keysWithSession.length} keys)`,
    );
    await snap("afterGrant");

    // ── 4. Byte-exact persistence round trip ────────────────────────────
    const serialized = serializeSession(session);
    writeFileSync(SESSION_FILE, serialized, { mode: 0o600 });
    const mode = statSync(SESSION_FILE).mode & 0o777;
    const restored = await deserializeSession(
      readFileSync(SESSION_FILE, "utf8"),
    );
    const reserialized = serializeSession(restored);
    assertStep(
      reserialized === serialized && mode === 0o600,
      "4.session-serde",
      `round-trip byte-identical (${serialized.length} bytes, file mode 0600)`,
      `byte drift (len ${serialized.length}→${reserialized.length}) or mode ${mode.toString(8)}`,
    );

    // ── 5. Session provider from the restored session ────────────────────
    sessionProvider = new AltanaWalletProvider({
      session: restored,
      network: altanaNetwork,
    });
    let grantRejected = false;
    try {
      await sessionProvider.grantSession({ permissions, expiry: 0 });
    } catch {
      grantRejected = true;
    }
    assertStep(
      sessionProvider.address.toLowerCase() === eoa.toLowerCase() &&
        sessionProvider.mode === "session" &&
        grantRejected,
      "5.session-provider",
      `address==wallet; grantSession correctly refused in session mode`,
      `address=${sessionProvider.address} mode=${sessionProvider.mode} grantRejected=${grantRejected}`,
    );

    // ── 6. ERC-8004 registerAgent through the session ────────────────────
    const registry = new ContractInterface({
      client: publicClient,
      contractAddress: protocolNetwork.registryContract,
      walletProvider: sessionProvider,
    });
    const agentUri = `https://bnbagent-sdk-e2e.invalid/altana/${Date.now()}.json`;
    const registered = await registry.registerAgent(agentUri);
    if (registered.agentId === null) {
      fail(
        "6.register-agent",
        `agentId not recovered from relay receipt (tx ${registered.transactionHash})`,
      );
    }
    const info = await registry.getAgentInfo(registered.agentId);
    assertStep(
      info.owner.toLowerCase() === eoa.toLowerCase(),
      "6.register-agent",
      `agentId=${registered.agentId} (parsed from relay receipt); owner==wallet`,
      `owner=${info.owner} wallet=${eoa} agentWallet=${info.agentWallet}`,
    );
    await snap("afterRegister");

    // ── 7. ERC-8183 createJob through the session ────────────────────────
    const jobs = await ERC8183Client.create({
      walletProvider: sessionProvider,
      network: protocolNetwork,
    });
    const expiredAt = BigInt(Math.floor(Date.now() / 1000) + 30 * 86_400);
    const created = await jobs.createJob({ expiredAt });
    if (created.jobId === null) {
      fail(
        "7.create-job",
        `jobId not recovered from relay receipt (tx ${created.transactionHash})`,
      );
    }
    const job = await jobs.getJob(created.jobId);
    assertStep(
      job.client.toLowerCase() === eoa.toLowerCase(),
      "7.create-job",
      `jobId=${created.jobId} (parsed from relay receipt); job.client==wallet`,
      `job.client=${job.client} wallet=${eoa}`,
    );
    await snap("afterCreateJob");

    // ── 8. cancelOpen via the session ────────────────────────────────────
    await jobs.cancelOpen(created.jobId);
    const cancelled = await jobs.getJob(created.jobId);
    assertStep(
      cancelled.status === JobStatus.REJECTED,
      "8.cancel-open",
      `job ${created.jobId} → REJECTED`,
      `status=${JobStatus[cancelled.status]} (${cancelled.status})`,
    );
    await snap("afterCancel");

    // ── 9. fund: bundled approve+fund in ONE relay intent (needs ≥0.1 U) ─
    const uBalance = await uBalanceOf();
    if (uBalance < FUND_AMOUNT) {
      skip(
        "9.fund",
        `wallet holds ${formatUnits(uBalance, 18)} U < 0.1 U — top up ${eoa} to exercise the fund path`,
      );
    } else {
      try {
        await fundLifecycle(jobs, expiredAt, eoa, publicClient);
      } catch (error) {
        if (error instanceof StepFailure) {
          throw error;
        }
        fail("9.fund", describeError(error));
      }
    }
    await snap("afterFund");

    // ── 10. revokeSession + negative verification ────────────────────────
    await adminProvider.revokeSession(session);
    const keysAfterRevoke = await activeKeys();
    const revokedFromRegistry = !keysAfterRevoke
      .map((k) => k.toLowerCase())
      .includes(keyId.toLowerCase());
    let sessionWriteFailed = false;
    let failureShape = "";
    try {
      // Fresh client to dodge any cached executor state: the revoked
      // session MUST be refused by the on-chain validator.
      const revokedJobs = await ERC8183Client.create({
        walletProvider: sessionProvider,
        network: protocolNetwork,
      });
      await revokedJobs.createJob({ expiredAt });
    } catch (error) {
      sessionWriteFailed = true;
      failureShape = String((error as Error).message).slice(0, 120);
    }
    assertStep(
      revokedFromRegistry && sessionWriteFailed,
      "10.revoke-session",
      `key gone from registry; post-revoke session write refused (${failureShape})`,
      `revokedFromRegistry=${revokedFromRegistry} sessionWriteFailed=${sessionWriteFailed}`,
    );
    await snap("afterRevoke");

    // ── 11. Fee accounting ───────────────────────────────────────────────
    const d = (a: string, b: string) =>
      formatEther((balances[a] ?? 0n) - (balances[b] ?? 0n));
    console.log("\n  Fee accounting (native deltas vs on-chain price):");
    console.log(
      `    on-chain registration fee: ${formatEther(feeWei)} tBNB/key (oracle-priced ~$0.50)`,
    );
    console.log(
      `    bootstrap x2:   -${d("beforeBootstrap", "afterBootstrap")} tBNB (reg fee only if first-ever; rest gas)`,
    );
    console.log(
      `    grantSession:   -${d("afterBootstrap", "afterGrant")} tBNB (1x reg fee + gas)`,
    );
    console.log(
      `    registerAgent:  -${d("afterGrant", "afterRegister")} tBNB (gas only)`,
    );
    console.log(
      `    createJob:      -${d("afterRegister", "afterCreateJob")} tBNB (gas only)`,
    );
    console.log(
      `    cancelOpen:     -${d("afterCreateJob", "afterCancel")} tBNB (gas only)`,
    );
    console.log(
      `    fund flow:      -${d("afterCancel", "afterFund")} tBNB (gas only; 0 if step 9 skipped)`,
    );
    console.log(
      `    revokeSession:  -${d("afterFund", "afterRevoke")} tBNB (gas only — protocol-free)`,
    );
    pass("11.fee-report", "deltas printed above for reconciliation");
  } finally {
    // ── 12. Cleanup (always) ─────────────────────────────────────────────
    try {
      rmSync(SESSION_FILE, { force: true });
      pass("12.cleanup", "removed .session.json");
    } catch (error) {
      console.error(`  ⚠️ [12.cleanup] ${String(error)}`);
    }
    console.log(`\n=== Summary ===\n  ${summary.join("\n  ")}`);
  }
}

main().then(
  () => {
    const failed = summary.some((s) => s.startsWith("FAIL"));
    process.exitCode = failed ? 1 : (process.exitCode ?? 0);
  },
  (error) => {
    if (!(error instanceof StepFailure)) {
      console.error(`\n  ❌ unexpected error: ${String(error)}`);
    }
    process.exitCode = 1;
  },
);

/**
 * Compile-time drift detection between `src/wallets/altana/types.ts` (the
 * SDK-shipped structural mirrors) and the real `@altananetwork/sdk` types
 * (a devDependency, imported type-only so nothing GPL enters the runtime
 * graph).
 *
 * The failure surface is `pnpm typecheck`, not the test runner: every
 * function below is an assignability assertion the compiler must accept.
 * Data types are pinned in BOTH directions (we build values with our types
 * and feed them to the SDK, and we receive SDK values into our types);
 * module/client shapes only need real → mirror, since our mirrors are a
 * deliberate subset of the SDK surface.
 */

import type * as AltanaSdk from "@altananetwork/sdk";
import type {
  Call,
  CallPermission,
  Client,
  ExecuteResult,
  NetworkConfig,
  Session,
  SessionPermissions,
  Signer,
  SpendPermission,
  Wallet,
} from "@altananetwork/sdk";
import { describe, expect, it } from "vitest";
import type {
  AltanaCall,
  AltanaCallPermission,
  AltanaExecuteResult,
  AltanaNetworkConfig,
  AltanaSdkClient,
  AltanaSdkModule,
  AltanaSession,
  AltanaSessionPermissions,
  AltanaSigner,
  AltanaSpendPermission,
  AltanaWallet,
} from "../src/wallets/altana/types.js";

// ── Data types: mutual assignability ──────────────────────────────────────

const signerToMirror = (v: Signer): AltanaSigner => v;
const signerToReal = (v: AltanaSigner): Signer => v;

const callPermissionToMirror = (v: CallPermission): AltanaCallPermission => v;
const callPermissionToReal = (v: AltanaCallPermission): CallPermission => v;

const spendPermissionToMirror = (v: SpendPermission): AltanaSpendPermission =>
  v;
const spendPermissionToReal = (v: AltanaSpendPermission): SpendPermission => v;

const permissionsToMirror = (v: SessionPermissions): AltanaSessionPermissions =>
  v;
const permissionsToReal = (v: AltanaSessionPermissions): SessionPermissions =>
  v;

const sessionToMirror = (v: Session): AltanaSession => v;
const sessionToReal = (v: AltanaSession): Session => v;

const networkToMirror = (v: NetworkConfig): AltanaNetworkConfig => v;
const networkToReal = (v: AltanaNetworkConfig): NetworkConfig => v;

const walletToMirror = (v: Wallet): AltanaWallet => v;
const walletToReal = (v: AltanaWallet): Wallet => v;

const callToMirror = (v: Call): AltanaCall => v;
const callToReal = (v: AltanaCall): Call => v;

const executeResultToMirror = (v: ExecuteResult): AltanaExecuteResult => v;
const executeResultToReal = (v: AltanaExecuteResult): ExecuteResult => v;

// ── Module/client: the real SDK must satisfy our internal subset ──────────

const clientToMirror = (v: Client): AltanaSdkClient => v;
const moduleToMirror = (v: typeof AltanaSdk): AltanaSdkModule => v;

describe("altana type mirrors", () => {
  it("stay assignable to and from @altananetwork/sdk (compile-time contract)", () => {
    // The assignability functions above ARE the assertions; tsc rejects
    // this file the moment the mirrors drift. Referencing them here keeps
    // them live under lint and gives vitest a runtime anchor.
    const witnesses = [
      signerToMirror,
      signerToReal,
      callPermissionToMirror,
      callPermissionToReal,
      spendPermissionToMirror,
      spendPermissionToReal,
      permissionsToMirror,
      permissionsToReal,
      sessionToMirror,
      sessionToReal,
      networkToMirror,
      networkToReal,
      walletToMirror,
      walletToReal,
      callToMirror,
      callToReal,
      executeResultToMirror,
      executeResultToReal,
      clientToMirror,
      moduleToMirror,
    ];
    for (const witness of witnesses) {
      expect(typeof witness).toBe("function");
    }
  });
});

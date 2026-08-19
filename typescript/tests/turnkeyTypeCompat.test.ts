/**
 * Compile-time drift detection between `src/wallets/turnkey/types.ts` (the
 * SDK-shipped structural mirrors) and the real `@turnkey/sdk-server` /
 * `@turnkey/viem` types (devDependencies, imported type-only so the
 * optional peers never enter the runtime graph).
 *
 * The failure surface is `pnpm typecheck`, not the test runner: every
 * function below is an assignability assertion the compiler must accept.
 * Module/class shapes are pinned real → mirror (our mirrors are a
 * deliberate subset of the vendor surface); the constructor config is
 * pinned mirror → real (we build the config and feed it to the vendor).
 */

import type * as TurnkeySdkServer from "@turnkey/sdk-server";
import type * as TurnkeyViem from "@turnkey/viem";
import { describe, expect, it } from "vitest";
import type {
  TurnkeyClientConfig,
  TurnkeySdkServerModule,
  TurnkeyViemModule,
} from "../src/wallets/turnkey/types.js";

// ── Module shapes: the real packages must satisfy our internal subset ─────

const sdkServerModuleToMirror = (
  v: typeof TurnkeySdkServer,
): TurnkeySdkServerModule => v;

const viemModuleToMirror = (v: typeof TurnkeyViem): TurnkeyViemModule => v;

// ── Constructor config: our config values feed the real constructor ───────

const configToReal = (
  v: TurnkeyClientConfig,
): ConstructorParameters<typeof TurnkeySdkServer.Turnkey>[0] => v;

describe("turnkey type mirrors", () => {
  it("stay assignable against @turnkey/sdk-server and @turnkey/viem (compile-time contract)", () => {
    // The assignability functions above ARE the assertions; tsc rejects
    // this file the moment the mirrors drift. Referencing them here keeps
    // them live under lint and gives vitest a runtime anchor.
    const witnesses = [
      sdkServerModuleToMirror,
      viemModuleToMirror,
      configToReal,
    ];
    for (const witness of witnesses) {
      expect(typeof witness).toBe("function");
    }
  });
});

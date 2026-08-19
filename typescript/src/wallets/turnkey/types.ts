/**
 * Structural mirrors of the `@turnkey/sdk-server` + `@turnkey/viem` public
 * types the provider touches.
 *
 * Both Turnkey packages are **optional** peer dependencies, so nothing in
 * `src/` may import from them at the type level — otherwise the generated
 * `.d.ts` bundle would carry `import("@turnkey/…")` references that break
 * consumers who never installed the peers. These mirrors keep our public
 * declaration files self-contained; `tests/turnkeyTypeCompat.test.ts` pins
 * assignability against the real packages (devDependencies) so any drift
 * fails `pnpm typecheck` instead of surfacing at runtime.
 *
 * Mirrored from `@turnkey/sdk-server@8.1.0` (`TurnkeySDKServerConfig`,
 * `TurnkeyServerSDK`) and `@turnkey/viem@0.14.34` (`createAccount`). Only
 * the slice the provider calls is mirrored — the packages export far more.
 */

import type { LocalAccount } from "viem";

/**
 * Constructor config for the Turnkey server SDK (mirrors
 * `TurnkeySDKServerConfig`, required fields only). The API key pair is a
 * locally held P-256 keypair that stamps (signs) every request body — the
 * private key is a client credential, never sent over the wire.
 */
export interface TurnkeyClientConfig {
  apiBaseUrl: string;
  apiPublicKey: string;
  apiPrivateKey: string;
  defaultOrganizationId: string;
}

/**
 * Opaque handle for the authenticated Turnkey API client returned by
 * `Turnkey#apiClient()`. The provider only threads it through to
 * `createAccount`, so no surface is mirrored.
 */
export type TurnkeyApiClient = object;

/** The `Turnkey` server SDK instance surface the provider uses. */
export interface TurnkeyServerSdk {
  apiClient(): TurnkeyApiClient;
}

/** Module shape of `@turnkey/sdk-server` (the slice the provider uses). */
export interface TurnkeySdkServerModule {
  Turnkey: new (config: TurnkeyClientConfig) => TurnkeyServerSdk;
}

/**
 * Module shape of `@turnkey/viem` (the slice the provider uses).
 *
 * `createAccount` returns a standard viem `LocalAccount` whose sign methods
 * round-trip through the Turnkey API; with `signWith` set to the account's
 * Ethereum address it performs no network call at construction.
 */
export interface TurnkeyViemModule {
  createAccount(input: {
    client: TurnkeyApiClient;
    organizationId: string;
    signWith: string;
    ethereumAddress?: string;
  }): Promise<LocalAccount>;
}

/** Both lazily imported Turnkey packages, loaded and cached as one unit. */
export interface TurnkeySdkModules {
  sdkServer: TurnkeySdkServerModule;
  viem: TurnkeyViemModule;
}

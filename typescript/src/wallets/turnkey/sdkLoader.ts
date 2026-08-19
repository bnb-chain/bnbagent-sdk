/**
 * Lazy loader for the optional `@turnkey/sdk-server` + `@turnkey/viem` peer
 * dependencies.
 *
 * Turnkey is a remote signing service; its SDK packages are declared as
 * *optional* peerDependencies and this dynamic `import()` pair is the SDK's
 * ONLY runtime coupling point to them: nothing Turnkey-related loads until a
 * `TurnkeyWalletProvider` actually needs the backend, and consumers who
 * never touch Turnkey never need the packages installed.
 *
 * The provider always needs both packages together (`@turnkey/sdk-server`
 * for the authenticated API client, `@turnkey/viem` for the signing
 * account), so they load behind a single seam and cache as one unit.
 */

import type { TurnkeySdkModules } from "./types.js";

/** The npm package providing the authenticated Turnkey API client. */
export const TURNKEY_SDK_SERVER_PACKAGE = "@turnkey/sdk-server";

/** The npm package providing the viem signing-account adapter. */
export const TURNKEY_VIEM_PACKAGE = "@turnkey/viem";

/** One of the two optional Turnkey packages the provider binds to. */
export type TurnkeyPackageName =
  | typeof TURNKEY_SDK_SERVER_PACKAGE
  | typeof TURNKEY_VIEM_PACKAGE;

/** Host-supplied loader for the optional Turnkey SDK packages. */
export type TurnkeySdkImporter = (pkg: TurnkeyPackageName) => Promise<unknown>;

const realImporter: TurnkeySdkImporter = (pkg) => import(pkg);

let importTurnkeyModule: TurnkeySdkImporter = realImporter;
let cachedModules: Promise<TurnkeySdkModules> | null = null;

/** Whether `error` is any runtime's flavor of "that module isn't installed". */
function isModuleNotFound(error: unknown): boolean {
  const code = (error as { code?: unknown } | null)?.code;
  if (code === "ERR_MODULE_NOT_FOUND" || code === "MODULE_NOT_FOUND") {
    return true;
  }
  const message = error instanceof Error ? error.message : String(error);
  return (
    /cannot find (module|package)/i.test(message) &&
    (message.includes(TURNKEY_SDK_SERVER_PACKAGE) ||
      message.includes(TURNKEY_VIEM_PACKAGE))
  );
}

/**
 * Import both Turnkey packages, caching the combined module promise for the
 * process lifetime. A missing install is rewritten into an actionable error
 * naming the exact `pnpm add` to run; the failed promise is NOT cached, so
 * the next call retries cleanly.
 */
export async function loadTurnkeySdk(): Promise<TurnkeySdkModules> {
  if (!cachedModules) {
    cachedModules = Promise.all([
      importTurnkeyModule(TURNKEY_SDK_SERVER_PACKAGE),
      importTurnkeyModule(TURNKEY_VIEM_PACKAGE),
    ]).then(
      ([sdkServer, viem]) =>
        ({ sdkServer, viem }) as unknown as TurnkeySdkModules,
      (error: unknown) => {
        cachedModules = null;
        if (isModuleNotFound(error)) {
          throw new Error(
            `The Turnkey wallet provider requires the optional peer dependencies '${TURNKEY_SDK_SERVER_PACKAGE}' and '${TURNKEY_VIEM_PACKAGE}' (not installed). Install them with: pnpm add ${TURNKEY_SDK_SERVER_PACKAGE} ${TURNKEY_VIEM_PACKAGE}`,
            { cause: error },
          );
        }
        throw error;
      },
    );
  }
  return cachedModules;
}

/**
 * Swap the dynamic-import implementation, or restore the package-relative
 * default with `null`.
 *
 * Hosts such as a globally installed CLI use this seam to resolve the
 * optional Turnkey packages from an agent project's own `node_modules`.
 * Changing the importer always clears the process cache so the next Turnkey
 * operation uses the newly selected module source.
 */
export function setTurnkeySdkImporter(
  importer: TurnkeySdkImporter | null,
): void {
  importTurnkeyModule = importer ?? realImporter;
  cachedModules = null;
}

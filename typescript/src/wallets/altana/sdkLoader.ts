/**
 * Lazy loader for the optional `@altananetwork/sdk` peer dependency.
 *
 * The Altana SDK is GPL-3.0-or-later and ESM-only, so it is declared as an
 * *optional* peerDependency and this dynamic `import()` is the SDK's ONLY
 * runtime coupling point to it: nothing Altana-related loads until an
 * `AltanaWalletProvider` actually needs the backend, and consumers who
 * never touch Altana never need the package installed.
 */

import type { AltanaSdkModule } from "./types.js";

/** The npm package the Altana provider binds to at runtime. */
export const ALTANA_SDK_PACKAGE = "@altananetwork/sdk";

type AltanaSdkImporter = () => Promise<unknown>;

const realImporter: AltanaSdkImporter = () => import(ALTANA_SDK_PACKAGE);

let importAltanaSdkModule: AltanaSdkImporter = realImporter;
let cachedModule: Promise<AltanaSdkModule> | null = null;

/** Whether `error` is any runtime's flavor of "that module isn't installed". */
function isModuleNotFound(error: unknown): boolean {
  const code = (error as { code?: unknown } | null)?.code;
  if (code === "ERR_MODULE_NOT_FOUND" || code === "MODULE_NOT_FOUND") {
    return true;
  }
  const message = error instanceof Error ? error.message : String(error);
  return (
    /cannot find (module|package)/i.test(message) &&
    message.includes(ALTANA_SDK_PACKAGE)
  );
}

/**
 * Import `@altananetwork/sdk`, caching the module promise for the process
 * lifetime. A missing install is rewritten into an actionable error naming
 * the exact `pnpm add` to run; the failed promise is NOT cached, so the
 * next call retries cleanly.
 */
export async function loadAltanaSdk(): Promise<AltanaSdkModule> {
  if (!cachedModule) {
    cachedModule = importAltanaSdkModule().then(
      (mod) => mod as unknown as AltanaSdkModule,
      (error: unknown) => {
        cachedModule = null;
        if (isModuleNotFound(error)) {
          throw new Error(
            `The Altana wallet provider requires the optional peer dependency '${ALTANA_SDK_PACKAGE}' (not installed). Install it with: pnpm add ${ALTANA_SDK_PACKAGE} — note it is licensed GPL-3.0-or-later and published as ESM-only.`,
            { cause: error },
          );
        }
        throw error;
      },
    );
  }
  return cachedModule;
}

/**
 * Drop the cached module promise so the next `loadAltanaSdk()` re-imports.
 * Test hook only — production code never needs it.
 */
export function _resetAltanaSdkCacheForTests(): void {
  cachedModule = null;
}

/**
 * Swap the dynamic-import implementation (or restore it with `null`).
 * Test hook only — lets the import-failure mapping be exercised without
 * fighting the test runner's module registry. Clears the cache either way.
 */
export function _setAltanaSdkImporterForTests(
  importer: AltanaSdkImporter | null,
): void {
  importAltanaSdkModule = importer ?? realImporter;
  cachedModule = null;
}

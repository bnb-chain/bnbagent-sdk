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
/** Exact vendor release exercised by this repository's type and runtime tests. */
export const ALTANA_SDK_TESTED_VERSION = "0.7.1";

/** Host-supplied loader for the optional ESM-only Altana SDK package. */
export type AltanaSdkImporter = () => Promise<unknown>;

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

function validateAltanaSdk(mod: unknown): AltanaSdkModule {
  const candidate = mod as Record<string, unknown> | null;
  const missing: string[] = [];
  if (!candidate || typeof candidate.createClient !== "function") {
    missing.push("createClient");
  }
  if (!candidate || typeof candidate.signerFromPrivateKey !== "function") {
    missing.push("signerFromPrivateKey");
  }
  if (
    !candidate ||
    typeof candidate.BNB !== "object" ||
    candidate.BNB === null
  ) {
    missing.push("BNB");
  }
  if (missing.length > 0) {
    throw new Error(
      `Incompatible ${ALTANA_SDK_PACKAGE} runtime: missing required export(s) ${missing.join(
        ", ",
      )}. Install the tested version exactly: pnpm add ${ALTANA_SDK_PACKAGE}@${ALTANA_SDK_TESTED_VERSION}`,
    );
  }
  return candidate as unknown as AltanaSdkModule;
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
      (mod) => {
        try {
          return validateAltanaSdk(mod);
        } catch (error) {
          cachedModule = null;
          throw error;
        }
      },
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
 * Swap the dynamic-import implementation, or restore the package-relative
 * default with `null`.
 *
 * Hosts such as a globally installed CLI use this seam to resolve the
 * optional, ESM-only `@altananetwork/sdk` from an agent project's own
 * `node_modules`. Changing the importer always clears the process cache so
 * the next Altana operation uses the newly selected module source.
 */
export function setAltanaSdkImporter(importer: AltanaSdkImporter | null): void {
  importAltanaSdkModule = importer ?? realImporter;
  cachedModule = null;
}

/** @deprecated Use {@link setAltanaSdkImporter}. */
export const _setAltanaSdkImporterForTests = setAltanaSdkImporter;

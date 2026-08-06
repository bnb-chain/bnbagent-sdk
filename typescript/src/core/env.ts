/**
 * Opt-in `.env` loading for applications built on the SDK.
 *
 * The SDK never calls {@link loadEnv} at import time (library discipline —
 * a library must not mutate the process environment as a side effect of
 * being imported). Applications and examples opt in explicitly, typically
 * as the first line of their entrypoint.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";
import { config as loadDotenv } from "dotenv";

/**
 * Load `.env.local` then `.env` from `root`, never overriding.
 *
 * Both files are loaded with `dotenv.config({ path, override: false })`, in
 * that exact order. The ordering is the whole trick: with `override: false`
 * the *first* loader to set a key wins, so loading the local file first
 * yields the precedence
 *
 *     real environment  >  .env.local  >  .env
 *
 * (Next.js semantics). The naive alternative — `.env` first with override
 * on the local file — would let a stale dev `.env.local` left in an image
 * stomp a deployment-injected secret such as `TWAK_WALLET_PASSWORD`: an
 * incident path, not a style choice.
 *
 * `root` defaults to `process.cwd()`; there is deliberately no upward
 * directory search (the SDK has no project-root marker — callers anchor
 * the lookup explicitly).
 *
 * @returns The list of files actually loaded (existing files, in load
 * order).
 */
export function loadEnv(root?: string): string[] {
  const base = root ?? process.cwd();
  const loaded: string[] = [];
  for (const name of [".env.local", ".env"]) {
    const path = join(base, name);
    if (existsSync(path)) {
      loadDotenv({ path, override: false });
      loaded.push(path);
    }
  }
  return loaded;
}

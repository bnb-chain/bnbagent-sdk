/**
 * Cold-start materialization of twak key material from a secrets manager.
 *
 * In deployment the encrypted twak wallet does not live on disk ahead of
 * time: the runtime pulls it from the secret bundle (`TWAK_WALLET_JSON`,
 * optionally `TWAK_CREDENTIALS_JSON`) and writes it under a writable
 * `home` before constructing `new TWAKProvider({ home, autoCreate:
 * false })`. Port of `python/bnbagent/wallets/twak_custody.py`.
 *
 * `wallet.json` is portable: an AES-256-GCM-encrypted mnemonic with no
 * machine-binding fields (field-verified against the twak source). It is
 * only ever handled in this encrypted form and lands with 0700 dirs /
 * 0600 files (INV-3); materialization is idempotent and never overwrites,
 * so a wallet can only come from the bundle — never be silently replaced
 * or re-minted (INV-4).
 */

import {
  chmodSync,
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  writeSync,
} from "node:fs";
import { join } from "node:path";

/** Options for {@link materializeTwakHome}. */
export interface MaterializeTwakHomeOpts {
  /** Content of the encrypted wallet file (`TWAK_WALLET_JSON`), verbatim. */
  walletJson: string;
  /** Optional content of the API-credentials file (`TWAK_CREDENTIALS_JSON`). */
  credentialsJson?: string;
  /** Writable directory to act as twak's `$HOME`. */
  home: string;
}

/**
 * Write twak key material under `<home>/.twak/` (idempotent — existing
 * files are never overwritten, INV-4). Returns `home`, ready to pass as
 * `TWAKProvider({ home })`.
 */
export function materializeTwakHome(opts: MaterializeTwakHomeOpts): string {
  const twakDir = join(opts.home, ".twak");
  mkdirSync(twakDir, { recursive: true });
  chmodSync(twakDir, 0o700); // explicit: mkdir's mode is subject to the umask
  writeSecret(join(twakDir, "wallet.json"), opts.walletJson);
  if (opts.credentialsJson !== undefined) {
    writeSecret(join(twakDir, "credentials.json"), opts.credentialsJson);
  }
  return opts.home;
}

/** Create `path` with 0600 and write `content`; skip if it exists. */
function writeSecret(path: string, content: string): void {
  if (existsSync(path)) {
    return; // never overwrite materialized key material
  }
  const fd = openSync(path, "wx", 0o600);
  try {
    chmodSync(path, 0o600); // explicit: openSync's mode is subject to the umask
    writeSync(fd, content);
  } finally {
    closeSync(fd);
  }
}

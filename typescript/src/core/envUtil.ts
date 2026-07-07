/**
 * Tiny env-var reader shared across SDK modules.
 *
 * All modules should use this instead of reading `process.env` directly so
 * the env surface stays auditable and prefix conventions stay consistent.
 */

/**
 * Read `<prefix><key>` from the environment.
 *
 * Returns `defaultValue` when the variable is unset or empty. Empty strings
 * are normalised to `undefined` (or `defaultValue`) so callers don't have to
 * distinguish `VAR=` from `VAR` unset.
 */
export function getEnv(
  key: string,
  defaultValue?: string,
  prefix = "",
): string | undefined {
  const fullKey = `${prefix}${key}`;
  const value = process.env[fullKey];
  if (value === undefined || value === "") {
    return defaultValue;
  }
  return value;
}

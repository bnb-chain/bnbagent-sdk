const MAX_VALUE_LENGTH = 64;

/** Render untrusted diagnostic values without multiline or unbounded output. */
export function describeValue(value: unknown): string {
  if (value === null || value === undefined) return "None";
  // Never serialize containers or invoke their toString/toJSON hooks. Even a
  // JSON array can hide an oversized string inside a non-string field name.
  if (typeof value === "object") {
    return Array.isArray(value) ? "[Array]" : "[Object]";
  }
  if (typeof value === "function") return "[Function]";
  if (typeof value === "symbol") return "[Symbol]";
  const text = String(value);
  const shown =
    text.length > MAX_VALUE_LENGTH
      ? `${text.slice(0, MAX_VALUE_LENGTH)}…(${text.length} chars)`
      : text;
  if (typeof value !== "string") return shown;
  // Truncate before escaping, including the Unicode line separators that
  // JSON.stringify leaves literal.
  return JSON.stringify(shown)
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

/** Keep a parser diagnostic without retaining its raw input-bearing stack/cause. */
export function summarizeParserError(error: unknown): Error {
  return new Error(
    describeValue(error instanceof Error ? error.message : error),
  );
}

/** Read a JSON object without allowing a remote response to exhaust memory. */
export async function boundedJson(
  response: Response,
  maxBytes: number,
  timeoutMs = 30_000,
): Promise<Record<string, unknown>> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("JSON response has no body");
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error("JSON response timed out"));
      void reader.cancel().catch(() => {});
    }, timeoutMs);
  });
  try {
    const length = response.headers.get("content-length");
    if (length && (!/^\d+$/.test(length) || Number(length) > maxBytes))
      throw new Error("JSON response exceeds byte limit");
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await Promise.race([reader.read(), timeout]);
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) throw new Error("JSON response exceeds byte limit");
      chunks.push(value);
    }
    const data: unknown = JSON.parse(
      Buffer.concat(chunks, size).toString("utf8"),
    );
    if (!data || typeof data !== "object" || Array.isArray(data))
      throw new Error("JSON response must be an object");
    return data as Record<string, unknown>;
  } finally {
    clearTimeout(timer);
    await reader.cancel().catch(() => {});
  }
}

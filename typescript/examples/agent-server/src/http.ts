/**
 * Tiny `node:http` router — the whole HTTP layer for this example, in one file.
 *
 * The SDK ships no serving runtime; this is deliberately a ~100-line,
 * zero-dependency shim (same `node:http` precedent as `examples/x402`) so the
 * example stays copy-and-own. For a real service swap this for your framework
 * of choice (Hono/Fastify/Express) — the route handlers below only touch
 * `IncomingMessage`/`ServerResponse`, so the port is mechanical.
 */

import type { IncomingMessage, ServerResponse } from "node:http";

/** Per-request context handed to a {@link Route} handler. */
export interface RouteContext {
  req: IncomingMessage;
  res: ServerResponse;
  /** Path params captured from the route pattern (e.g. `:id`). */
  params: Record<string, string>;
  /** Parsed JSON body for the request, or `undefined` if none/invalid. */
  body: unknown;
  /** Best-effort client IP (for rate limiting). */
  clientIp: string;
}

export type RouteHandler = (ctx: RouteContext) => Promise<void> | void;

/** A compiled route: HTTP method + path matcher + handler. */
export interface Route {
  method: string;
  pattern: RegExp;
  keys: string[];
  handler: RouteHandler;
}

/**
 * Build a {@link Route} from an Express-style path (`/job/:id/response`).
 *
 * `:name` segments become named capture groups; everything else is matched
 * literally. Trailing slashes are tolerated at match time.
 */
export function route(
  method: string,
  path: string,
  handler: RouteHandler,
): Route {
  const keys: string[] = [];
  const source = path
    .replace(/[.*+?^${}()|[\]\\]/g, "\\$&") // escape regex metachars (":" is not one)
    .replace(/:([A-Za-z_][A-Za-z0-9_]*)/g, (_m, key: string) => {
      keys.push(key);
      return "([^/]+)";
    });
  return {
    method: method.toUpperCase(),
    pattern: new RegExp(`^${source}/?$`),
    keys,
    handler,
  };
}

/** Send a JSON response with the given status code. */
export function sendJson(
  res: ServerResponse,
  status: number,
  body: unknown,
): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

export class HttpInputError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Bound streamed requests as well as Content-Length; deadline covers the body. */
export function readJsonBody(
  req: IncomingMessage,
  maxBytes = 64 * 1024,
  timeoutMs = 10_000,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    const finish = (error?: Error) => {
      clearTimeout(timer);
      req.removeListener("data", onData);
      req.removeListener("end", onEnd);
      req.removeListener("error", onError);
      req.removeListener("aborted", onAborted);
      if (error) {
        req.pause();
        reject(error);
        return;
      }
      try {
        resolve(
          size
            ? JSON.parse(Buffer.concat(chunks, size).toString("utf8"))
            : undefined,
        );
      } catch {
        reject(new HttpInputError("Invalid JSON", 400));
      }
    };
    const onData = (value: Buffer | string) => {
      const chunk = Buffer.isBuffer(value) ? value : Buffer.from(value);
      size += chunk.length;
      if (size > maxBytes)
        return finish(
          new HttpInputError("Request body exceeds byte limit", 413),
        );
      chunks.push(chunk);
    };
    const onEnd = () => finish();
    const onError = () =>
      finish(new HttpInputError("Request body unavailable", 400));
    const onAborted = () => finish(new HttpInputError("Request aborted", 400));
    const timer = setTimeout(
      () => finish(new HttpInputError("Request body timed out", 408)),
      timeoutMs,
    );
    const length = req.headers["content-length"];
    if (length && (!/^\d+$/.test(length) || Number(length) > maxBytes)) {
      finish(new HttpInputError("Request body exceeds byte limit", 413));
      return;
    }
    req.on("data", onData);
    req.once("end", onEnd);
    req.once("error", onError);
    req.once("aborted", onAborted);
  });
}

function normalizeIp(value: string): string {
  const ip = value.trim();
  if (ip.startsWith("::ffff:")) {
    return ip.slice("::ffff:".length);
  }
  if (ip.startsWith("[") && ip.endsWith("]")) {
    return ip.slice(1, -1);
  }
  return ip;
}

/**
 * Resolve the request IP without trusting client-controlled forwarding headers.
 *
 * `X-Forwarded-For` is considered only when the socket peer is explicitly
 * trusted. The chain is then walked from right to left, returning the first
 * untrusted hop. This is the same trust boundary used by production proxy-aware
 * frameworks and prevents callers from minting fresh rate-limit buckets.
 */
export function clientIpOf(
  req: IncomingMessage,
  trustedProxyIps: ReadonlySet<string> = new Set(),
): string {
  const peer = normalizeIp(req.socket.remoteAddress ?? "unknown");
  const trusted = new Set([...trustedProxyIps].map(normalizeIp));
  if (!trusted.has(peer)) {
    return peer;
  }

  const rawForwarded = req.headers["x-forwarded-for"];
  const forwarded = Array.isArray(rawForwarded)
    ? rawForwarded.join(",")
    : rawForwarded;
  if (!forwarded) {
    return peer;
  }

  const hops = forwarded.split(",").map(normalizeIp).filter(Boolean);
  for (let i = hops.length - 1; i >= 0; i -= 1) {
    const hop = hops[i];
    if (hop && !trusted.has(hop)) {
      return hop;
    }
  }
  return hops[0] ?? peer;
}

export interface RequestListenerOptions {
  /** Exact socket peer IPs allowed to supply `X-Forwarded-For`. */
  trustedProxyIps?: ReadonlySet<string>;
  /** Admission check runs before buffering or parsing mutation bodies. */
  beforeBody?: (
    req: IncomingMessage,
    res: ServerResponse,
    clientIp: string,
  ) => Promise<boolean> | boolean;
}

/**
 * Compose routes into a `node:http` request listener.
 *
 * Matches on method + path (first match wins), parses a JSON body once, and
 * returns 404 (`not_found`) / 405 (`method_not_allowed`) for misses. Any
 * throw inside a handler becomes a 500 (`internal_error`) instead of crashing
 * the process.
 */
export function makeRequestListener(
  routes: Route[],
  options: RequestListenerOptions = {},
) {
  const trustedProxyIps = options.trustedProxyIps ?? new Set<string>();
  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const path = url.pathname;
    const method = (req.method ?? "GET").toUpperCase();

    let pathMatchedOtherMethod = false;
    for (const r of routes) {
      const match = r.pattern.exec(path);
      if (!match) {
        continue;
      }
      if (r.method !== method) {
        pathMatchedOtherMethod = true;
        continue;
      }
      const params: Record<string, string> = {};
      r.keys.forEach((key, i) => {
        params[key] = decodeURIComponent(match[i + 1] ?? "");
      });
      const clientIp = clientIpOf(req, trustedProxyIps);
      try {
        if (
          (method === "POST" || method === "PUT" || method === "PATCH") &&
          options.beforeBody &&
          !(await options.beforeBody(req, res, clientIp))
        )
          return;
        const body =
          method === "POST" || method === "PUT" || method === "PATCH"
            ? await readJsonBody(req)
            : undefined;
        await r.handler({
          req,
          res,
          params,
          body,
          clientIp,
        });
      } catch (error) {
        if (!res.headersSent) {
          if (error instanceof HttpInputError)
            res.setHeader("Connection", "close");
          sendJson(res, error instanceof HttpInputError ? error.status : 500, {
            error: error instanceof Error ? error.message : "Internal error",
            error_code: "internal_error",
          });
        }
      }
      return;
    }

    sendJson(res, pathMatchedOtherMethod ? 405 : 404, {
      error: pathMatchedOtherMethod ? "Method not allowed" : "Not found",
      error_code: pathMatchedOtherMethod ? "method_not_allowed" : "not_found",
    });
  };
}

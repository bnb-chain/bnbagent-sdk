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

/** Read the full request body and parse it as JSON (`undefined` on empty/invalid). */
async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(chunk as Buffer);
  }
  if (chunks.length === 0) {
    return undefined;
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    return undefined;
  }
}

function clientIpOf(req: IncomingMessage): string {
  const fwd = req.headers["x-forwarded-for"];
  if (typeof fwd === "string" && fwd.length > 0) {
    return fwd.split(",")[0]?.trim() ?? "unknown";
  }
  return req.socket.remoteAddress ?? "unknown";
}

/**
 * Compose routes into a `node:http` request listener.
 *
 * Matches on method + path (first match wins), parses a JSON body once, and
 * returns 404 (`not_found`) / 405 (`method_not_allowed`) for misses. Any
 * throw inside a handler becomes a 500 (`internal_error`) instead of crashing
 * the process.
 */
export function makeRequestListener(routes: Route[]) {
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
      const body =
        method === "POST" || method === "PUT" || method === "PATCH"
          ? await readJsonBody(req)
          : undefined;
      try {
        await r.handler({ req, res, params, body, clientIp: clientIpOf(req) });
      } catch (error) {
        if (!res.headersSent) {
          sendJson(res, 500, {
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

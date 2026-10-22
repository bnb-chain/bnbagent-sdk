import { lookup as dnsLookup } from "node:dns/promises";
import * as http from "node:http";
import * as https from "node:https";
import { BlockList, isIP } from "node:net";
import { performance } from "node:perf_hooks";

// Direct public downloads for untrusted agentURI / deliverable URLs only.
// Operator-configured private endpoints use their own clients.
// Policy: https://www.iana.org/assignments/iana-ipv6-special-registry/

/** Cloud metadata hostnames blocked outright, before any DNS resolution. */
const BLOCKED_HOSTNAMES = new Set([
  "metadata.google.internal",
  "metadata.goog",
  "169.254.169.254",
]);

/**
 * Upper bound on the agent-URI HTTP body we will buffer + JSON-parse. The
 * remote endpoint is attacker-influenced (agentURI is on-chain metadata), so
 * an unbounded response could exhaust memory.
 */
const MAX_AGENT_URI_BYTES = 1 * 1024 * 1024; // 1 MB

/** DNS resolution timeout (ms) — an adversarial DNS server must not hang the caller. */
const DNS_TIMEOUT_MS = 5_000;

/** HTTP request timeout (ms) for fetching a parsed agent URI. */
const HTTP_TIMEOUT_MS = 10_000;

let cachedBlockList: BlockList | null = null;
let pendingDns = 0;

async function resolveHost(host: string) {
  if (isIP(host)) return [{ address: host, family: isIP(host) }];
  if (pendingDns >= 16) throw new Error("Public DNS resolver is busy");
  pendingDns++;
  try {
    return await dnsLookup(host, { all: true });
  } finally {
    pendingDns--;
  }
}

/**
 * Build (once) the `net.BlockList` of private/loopback/link-local/reserved
 * and RFC 6598 CGNAT ranges the SSRF guard refuses to connect to.
 *
 * Matches Python's explicit public_http.py policy across runtime versions.
 * Known address-translation/tunnel ranges are deliberately refused.
 */
function getBlockList(): BlockList {
  if (cachedBlockList) {
    return cachedBlockList;
  }
  const bl = new BlockList();
  // IPv4 private ranges (RFC 1918).
  bl.addSubnet("10.0.0.0", 8, "ipv4");
  bl.addSubnet("172.16.0.0", 12, "ipv4");
  bl.addSubnet("192.168.0.0", 16, "ipv4");
  // Loopback.
  bl.addSubnet("127.0.0.0", 8, "ipv4");
  // Link-local (includes the 169.254.169.254 cloud metadata address).
  bl.addSubnet("169.254.0.0", 16, "ipv4");
  // RFC 6598 Carrier-Grade NAT (covers Alibaba Cloud ECS metadata at
  // 100.100.100.200, which is NOT private/loopback/link-local).
  bl.addSubnet("100.64.0.0", 10, "ipv4");
  // "This network" / reserved-for-future-use / broadcast.
  bl.addSubnet("0.0.0.0", 8, "ipv4");
  bl.addSubnet("240.0.0.0", 4, "ipv4");
  bl.addAddress("255.255.255.255", "ipv4");
  // IETF protocol assignments / benchmarking / documentation ranges that
  // Python's `ipaddress.is_private`/`is_reserved` also refuse. Not normally
  // internally routed, but included for parity with the Python SSRF guard.
  bl.addSubnet("192.0.0.0", 24, "ipv4"); // IETF protocol assignments (covers 192.0.0.0/29, NAT64 discovery)
  bl.addSubnet("192.0.2.0", 24, "ipv4"); // TEST-NET-1 (documentation)
  bl.addSubnet("198.18.0.0", 15, "ipv4"); // network benchmarking
  bl.addSubnet("198.51.100.0", 24, "ipv4"); // TEST-NET-2 (documentation)
  bl.addSubnet("203.0.113.0", 24, "ipv4"); // TEST-NET-3 (documentation)
  // IPv6 loopback, unique-local, and link-local.
  bl.addAddress("::1", "ipv6");
  bl.addSubnet("fc00::", 7, "ipv6");
  bl.addSubnet("fe80::", 10, "ipv6");
  bl.addSubnet("224.0.0.0", 4, "ipv4");
  bl.addSubnet("192.88.99.0", 24, "ipv4");
  // Conservative native-unicast policy, shared with Python public_http.py.
  // Excludes NAT64, Teredo, 6to4, documentation and IETF special-use space.
  bl.addSubnet("2001::", 23, "ipv6");
  bl.addSubnet("2001:db8::", 32, "ipv6");
  bl.addSubnet("2002::", 16, "ipv6");
  bl.addSubnet("3fff::", 20, "ipv6");
  cachedBlockList = bl;
  return bl;
}

/** Unmap an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) to its IPv4 form. */
function unmapIpv4(address: string): string {
  const match = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(address);
  return match ? match[1] : address;
}

/** Whether `ip` falls in a blocked (private/loopback/link-local/reserved/CGNAT) range. */
export function isBlockedIp(ip: string): boolean {
  if (ip.includes("%") || !isIP(ip)) return true;
  const unmapped = unmapIpv4(ip);
  const type = unmapped.includes(":") ? "ipv6" : "ipv4";
  if (unmapped === "169.254.169.254") {
    return true;
  }
  if (type === "ipv6") {
    const allowed = new BlockList();
    allowed.addSubnet("2000::", 3, "ipv6");
    allowed.addSubnet("::ffff:0:0", 96, "ipv6");
    if (!allowed.check(unmapped, "ipv6")) return true;
  }
  return getBlockList().check(unmapped, type);
}

/**
 * Fetch `resolvedIp` over HTTP(S), sending the original `Host` header so the
 * remote server routes correctly, without following redirects, bounded to
 * {@link HTTP_TIMEOUT_MS} and a {@link MAX_AGENT_URI_BYTES} streamed cap.
 *
 * Never rejects: any failure (timeout, non-2xx status, oversized body,
 * transport error) resolves to `null`.
 */
function fetchViaResolvedIp(params: {
  isHttps: boolean;
  resolvedIp: string;
  port: number;
  path: string;
  hostname: string;
  hostHeader: string;
  maxBytes: number;
  timeoutMs: number;
}): Promise<Buffer | null> {
  const {
    isHttps,
    resolvedIp,
    port,
    path,
    hostname,
    hostHeader,
    maxBytes,
    timeoutMs,
  } = params;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: Buffer | null) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(value);
      }
    };

    const mod = isHttps ? https : http;
    const req = mod.request(
      {
        host: resolvedIp,
        port,
        path: path || "/",
        method: "GET",
        headers: { Host: hostHeader, "Accept-Encoding": "identity" },
        timeout: timeoutMs,
        agent: false,
        // Connect to the DNS-resolved IP (preventing a rebind between the
        // check and the request) while still presenting the original
        // hostname for TLS server-name/cert-hostname validation.
        ...(isHttps ? { servername: hostname } : {}),
      },
      (res) => {
        const status = res.statusCode ?? 0;
        if (status < 200 || status >= 300) {
          res.destroy();
          finish(null);
          return;
        }
        if ((res.headers["content-encoding"] ?? "identity") !== "identity") {
          res.destroy();
          finish(null);
          return;
        }
        const contentLength = res.headers["content-length"];
        if (
          contentLength &&
          (!/^\d+$/.test(contentLength) || Number(contentLength) > maxBytes)
        ) {
          res.destroy();
          finish(null);
          return;
        }
        const chunks: Buffer[] = [];
        let total = 0;
        res.on("data", (chunk: Buffer) => {
          if (settled) return;
          total += chunk.length;
          if (total > maxBytes) {
            res.destroy();
            finish(null);
            return;
          }
          chunks.push(chunk);
        });
        res.on("end", () => finish(Buffer.concat(chunks)));
        res.on("error", () => finish(null));
        res.on("aborted", () => finish(null));
        res.on("close", () => finish(null));
      },
    );
    req.on("timeout", () => {
      req.destroy();
      finish(null);
    });
    req.on("error", () => finish(null));
    const timer = setTimeout(() => {
      req.destroy();
      finish(null);
    }, timeoutMs);
    req.end();
  });
}

/**
 * SSRF-guarded fetch + JSON-parse of an `http(s)://` agent URI.
 *
 * Resolves the hostname, rejects blocked hostnames/IP ranges, then issues
 * the request against the resolved IP (not the hostname) to close the
 * DNS-rebinding window between check and use. Any failure returns `null`.
 */
export async function fetchPublicJson(
  agentUri: string,
  opts: { maxBytes?: number; timeoutMs?: number } = {},
): Promise<Record<string, unknown> | null> {
  const maxBytes = opts.maxBytes ?? MAX_AGENT_URI_BYTES;
  const timeoutMs = opts.timeoutMs ?? HTTP_TIMEOUT_MS;
  if (
    !Number.isSafeInteger(maxBytes) ||
    maxBytes <= 0 ||
    !Number.isFinite(timeoutMs) ||
    timeoutMs <= 0
  )
    return null;
  const deadline = performance.now() + timeoutMs;
  if (
    agentUri.length > 4096 ||
    Array.from(agentUri).some(
      (c) => c.charCodeAt(0) <= 32 || c.charCodeAt(0) === 127 || c === "\\",
    )
  )
    return null;
  let parsed: URL;
  try {
    parsed = new URL(agentUri);
  } catch {
    return null;
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.port === "0"
  )
    return null;
  const hostname = parsed.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!hostname) {
    return null;
  }
  if (BLOCKED_HOSTNAMES.has(hostname.toLowerCase())) {
    return null;
  }

  let resolvedIp: string;
  let dnsTimer: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeout = new Promise<never>((_, reject) => {
      dnsTimer = setTimeout(
        () => reject(new Error("dns lookup timed out")),
        Math.min(DNS_TIMEOUT_MS, timeoutMs),
      );
    });
    // `{ all: true }` returns every address the resolver reports for this
    // hostname (a name can have multiple A/AAAA records). Mirrors Python's
    // `getaddrinfo` iteration, which rejects if ANY returned address is
    // private/reserved — not just the one we happen to connect to.
    const results = await Promise.race([resolveHost(hostname), timeout]);
    if (results.length === 0 || results.some((r) => isBlockedIp(r.address))) {
      return null;
    }
    resolvedIp = results[0].address;
  } catch {
    return null;
  } finally {
    clearTimeout(dnsTimer);
  }

  const isHttps = parsed.protocol === "https:";
  const port = parsed.port ? Number(parsed.port) : isHttps ? 443 : 80;
  const hostHeader = parsed.host;

  if (performance.now() >= deadline) return null;
  try {
    const body = await fetchViaResolvedIp({
      isHttps,
      resolvedIp,
      port,
      path: `${parsed.pathname}${parsed.search}`,
      hostname,
      hostHeader,
      maxBytes,
      timeoutMs: deadline - performance.now(),
    });
    if (body === null) {
      return null;
    }
    const data = JSON.parse(body.toString("utf-8"));
    return data !== null && typeof data === "object" && !Array.isArray(data)
      ? data
      : null;
  } catch {
    return null;
  }
}

export function publicGatewayUrl(url: string, gateway: string): string {
  if (!url.startsWith("ipfs://")) return url;
  const cid = url.slice(7);
  if (
    cid.length > 128 ||
    !/^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})$/.test(cid)
  )
    throw new Error("Invalid IPFS CID");
  const parsed = new URL(gateway);
  if (parsed.search || parsed.hash) throw new Error("Invalid IPFS gateway");
  return `${gateway.replace(/\/+$/, "")}/${cid}`;
}

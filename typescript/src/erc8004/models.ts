/**
 * Data models for the ERC-8004 client.
 *
 * Port of `python/bnbagent/erc8004/models.py`'s `AgentEndpoint` dataclass.
 */

/** Options accepted by the {@link AgentEndpoint} constructor. */
export interface AgentEndpointOpts {
  name: string;
  endpoint: string;
  version?: string | null;
  capabilities?: string[] | null;
}

/**
 * Agent endpoint configuration.
 *
 * - `name` — protocol name (e.g. `"A2A"`, `"MCP"`, `"web"`).
 * - `endpoint` — endpoint URL; must start with `http://` or `https://`.
 * - `version` — optional protocol version.
 * - `capabilities` — optional list of capabilities.
 */
export class AgentEndpoint {
  /**
   * A2A discovery document path (A2A spec): the agent card is served at
   * `{base}/.well-known/agent-card.json`.
   */
  static readonly A2A_WELL_KNOWN_PATH = "/.well-known/agent-card.json";

  readonly name: string;
  readonly endpoint: string;
  readonly version: string | null;
  readonly capabilities: string[];

  constructor(opts: AgentEndpointOpts) {
    const { name, endpoint, version = null, capabilities = [] } = opts;
    if (!name || typeof name !== "string") {
      throw new Error("name is required and must be a string");
    }
    if (!endpoint || typeof endpoint !== "string") {
      throw new Error("endpoint is required and must be a string");
    }
    if (!(endpoint.startsWith("http://") || endpoint.startsWith("https://"))) {
      throw new Error("endpoint must start with http:// or https://");
    }
    this.name = name;
    this.endpoint = endpoint;
    this.version = version ?? null;
    this.capabilities = capabilities ?? [];
  }

  /** Convert to a plain object for JSON serialization; omits null/empty fields. */
  toDict(): Record<string, unknown> {
    const result: Record<string, unknown> = {
      name: this.name,
      endpoint: this.endpoint,
    };
    if (this.version !== null) {
      result.version = this.version;
    }
    if (this.capabilities.length > 0) {
      result.capabilities = this.capabilities;
    }
    return result;
  }

  /** Create from a plain object; requires `name` and `endpoint` fields. */
  static fromDict(data: Record<string, unknown>): AgentEndpoint {
    if (!("name" in data) || !("endpoint" in data)) {
      throw new Error("dictionary must contain 'name' and 'endpoint' fields");
    }
    return new AgentEndpoint({
      name: data.name as string,
      endpoint: data.endpoint as string,
      version: (data.version as string | undefined) ?? null,
      capabilities: (data.capabilities as string[] | undefined) ?? [],
    });
  }

  // ── Protocol-aware constructors (registration side only) ──
  //
  // The SDK does NOT implement the A2A or MCP runtimes — agents bring their
  // own serving stack. These constructors encode exactly what the EIP-8004
  // registration-file format specifies for each endpoint type, so callers
  // don't hand-roll stringly-typed entries.

  /**
   * A2A endpoint for the agent served at `baseUrl`.
   *
   * Appends the spec-defined agent-card discovery path
   * (`/.well-known/agent-card.json`) unless `baseUrl` already ends with it,
   * so the registered endpoint is always the discovery document a buyer can
   * fetch directly. The append is query-string-aware: an AgentCore invoke URL
   * carries `?qualifier=DEFAULT`, and naive concatenation would wedge the path
   * after the query (`…/invocations?qualifier=DEFAULT/.well-known/…`) and yield
   * an invalid URL — the path is inserted before the query/fragment instead.
   */
  static a2a(
    baseUrl: string,
    opts: { version?: string | null; capabilities?: string[] | null } = {},
  ): AgentEndpoint {
    return new AgentEndpoint({
      name: "A2A",
      endpoint: appendAgentCardPath(baseUrl),
      version: opts.version ?? null,
      capabilities: opts.capabilities ?? [],
    });
  }

  /**
   * MCP endpoint for a remote MCP server at `url`.
   *
   * Per the ERC-8004 registration-file format, an MCP entry is the server
   * URL plus an optional protocol `version` — nothing more. Only
   * network-transport MCP servers are registrable; a stdio MCP server has no
   * URL, which the endpoint's `http(s)://` validation enforces structurally.
   */
  static mcp(
    url: string,
    opts: { version?: string | null; capabilities?: string[] | null } = {},
  ): AgentEndpoint {
    return new AgentEndpoint({
      name: "MCP",
      endpoint: url,
      version: opts.version ?? null,
      capabilities: opts.capabilities ?? [],
    });
  }
}

/**
 * Append the A2A agent-card discovery path to `baseUrl`, preserving any query
 * string and fragment. Absolute URLs are parsed so the path is inserted before
 * the query (`…/invocations?qualifier=DEFAULT` →
 * `…/invocations/.well-known/agent-card.json?qualifier=DEFAULT`); inputs that
 * do not parse as absolute URLs fall back to trailing-slash-trimmed
 * concatenation. In both forms a URL already ending in the discovery path is
 * returned unchanged.
 */
function appendAgentCardPath(baseUrl: string): string {
  const wellKnown = AgentEndpoint.A2A_WELL_KNOWN_PATH;
  const raw = String(baseUrl ?? "").trim();
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    const trimmed = raw.replace(/\/+$/, "");
    return trimmed.endsWith(wellKnown) ? trimmed : `${trimmed}${wellKnown}`;
  }
  let path = parsed.pathname.replace(/\/+$/, "");
  if (!path.endsWith(wellKnown)) {
    path = path ? `${path}${wellKnown}` : wellKnown;
  }
  return `${parsed.protocol}//${parsed.host}${path}${parsed.search}${parsed.hash}`;
}

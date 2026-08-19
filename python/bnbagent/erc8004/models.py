"""
Data models for ERC8004Agent SDK.

Defines data structures for agent registration and endpoint configurations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


@dataclass
class AgentEndpoint:
    """
    Agent endpoint configuration.

    Attributes:
        name: Protocol name (e.g., "A2A", "MCP", "web")
        endpoint: Endpoint URL
        version: Optional protocol version
        capabilities: Optional list of capabilities

    Example:
        >>> endpoint = AgentEndpoint(
        ...     name="A2A",
        ...     endpoint="https://agent.example/.well-known/agent-card.json",
        ...     version="0.3.0"
        ... )
    """

    name: str
    endpoint: str
    version: str | None = None
    capabilities: list[str] | None = field(default_factory=list)

    def __post_init__(self):
        """Validate endpoint configuration."""
        if not self.name or not isinstance(self.name, str):
            raise ValueError("name is required and must be a string")
        if not self.endpoint or not isinstance(self.endpoint, str):
            raise ValueError("endpoint is required and must be a string")
        if not (self.endpoint.startswith("http://") or self.endpoint.startswith("https://")):
            raise ValueError("endpoint must start with http:// or https://")

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "endpoint": self.endpoint,
        }
        if self.version is not None:
            result["version"] = self.version
        if self.capabilities:
            result["capabilities"] = self.capabilities
        return result

    @classmethod
    def from_dict(cls, data: dict) -> AgentEndpoint:
        """
        Create from dictionary.

        Args:
            data: Dictionary with 'name' and 'endpoint' fields

        Returns:
            AgentEndpoint instance
        """
        if "name" not in data or "endpoint" not in data:
            raise ValueError("dictionary must contain 'name' and 'endpoint' fields")
        return cls(
            name=data["name"],
            endpoint=data["endpoint"],
            version=data.get("version"),
            capabilities=data.get("capabilities", []),
        )

    # ── Protocol-aware constructors (registration side only) ──
    #
    # The SDK does NOT implement the A2A or MCP runtimes — agents bring their
    # own serving stack. These constructors encode exactly what the EIP-8004
    # registration-file format specifies for each endpoint type (its examples
    # name A2A, MCP and OASF verbatim), so callers don't hand-roll
    # stringly-typed entries. Address-format facts only — never the other
    # protocol's behavior, never a dependency on its SDK.

    #: A2A discovery document path (A2A spec): the agent card is served at
    #: ``{base}/.well-known/agent-card.json``.
    A2A_WELL_KNOWN_PATH = "/.well-known/agent-card.json"

    @classmethod
    def a2a(
        cls,
        base_url: str,
        *,
        version: str | None = None,
        capabilities: list[str] | None = None,
    ) -> AgentEndpoint:
        """A2A endpoint for the agent served at ``base_url``.

        Appends the spec-defined agent-card discovery path
        (``/.well-known/agent-card.json``) unless ``base_url`` already ends
        with it, so the registered endpoint is always the discovery document
        a buyer can fetch directly. The append is query-string-aware: an
        AgentCore invoke URL carries ``?qualifier=DEFAULT``, and naive
        concatenation would wedge the path after the query
        (``…/invocations?qualifier=DEFAULT/.well-known/…``) and yield an
        invalid URL — the path is inserted before the query/fragment instead.

        Example:
            >>> endpoint = AgentEndpoint.a2a("https://agent.example")
            >>> endpoint.endpoint
            'https://agent.example/.well-known/agent-card.json'
        """
        return cls(
            name="A2A",
            endpoint=cls._append_agent_card_path(base_url),
            version=version,
            capabilities=list(capabilities or []),
        )

    @classmethod
    def _append_agent_card_path(cls, base_url: str) -> str:
        """Append the A2A discovery path, preserving any query/fragment.

        Absolute URLs are parsed so the path lands before the query; inputs
        that are not absolute URLs fall back to trailing-slash-trimmed
        concatenation. A URL already ending in the discovery path is returned
        unchanged in both forms.
        """
        raw = (base_url or "").strip()
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            url = raw.rstrip("/")
            if not url.endswith(cls.A2A_WELL_KNOWN_PATH):
                url += cls.A2A_WELL_KNOWN_PATH
            return url
        path = parts.path.rstrip("/")
        if not path.endswith(cls.A2A_WELL_KNOWN_PATH):
            path += cls.A2A_WELL_KNOWN_PATH
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    @classmethod
    def mcp(
        cls,
        url: str,
        *,
        version: str | None = None,
        capabilities: list[str] | None = None,
    ) -> AgentEndpoint:
        """MCP endpoint for a remote MCP server at ``url``.

        Per the ERC-8004 registration-file format, an MCP entry is the server
        URL plus an optional protocol ``version`` (e.g. ``"2025-06-18"``) —
        nothing more. Only network-transport MCP servers are registrable; a
        stdio MCP server has no URL, which the endpoint's ``http(s)://``
        validation enforces structurally.

        Example:
            >>> AgentEndpoint.mcp("https://agent.example/mcp", version="2025-06-18")
            AgentEndpoint(name='MCP', endpoint='https://agent.example/mcp', ...)
        """
        return cls(
            name="MCP",
            endpoint=url,
            version=version,
            capabilities=list(capabilities or []),
        )

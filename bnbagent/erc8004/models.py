"""
Data models for ERC8004Agent SDK.

Defines data structures for agent registration and endpoint configurations.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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

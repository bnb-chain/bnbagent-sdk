"""Port adapters: the only code that knows concrete chain SDKs / protocols.

- ``integrations.bnbagent``: the ports on the bnbagent SDK (``[bnbagent]`` extra); the only
  module allowed to import the SDK or web3.
- ``integrations.jsonrpc``: read-only ports over plain JSON-RPC (httpx; ``[evm]`` for ABI).

Importing this package imports no SDK.
"""

from __future__ import annotations

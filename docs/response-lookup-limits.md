# Response lookup limits (SRC-1623)

The Python HTTP reference limits `/job/{id}/response` separately from quote
negotiation. Defaults are 60 requests per client IP and 300 per process in a
60-second window. Configure `ERC8183_RESPONSE_RATE_LIMIT`,
`ERC8183_RESPONSE_GLOBAL_RATE_LIMIT`, and `ERC8183_RESPONSE_RATE_WINDOW`.
Rejected requests return 429 before invoking the SDK or RPC.

For multiple replicas, inject both `response_limiter` and
`global_response_limiter` into `create_erc8183_app`, using the existing sync or
async `RateLimiter` protocol backed by shared storage, or enforce equivalent
limits at the gateway. SDK/example defaults are per process. The application
uses `request.client.host`; configure trusted reverse proxies in the ASGI server
rather than accepting arbitrary forwarded headers in this route.

`ERC8183JobOps` additionally shares concurrent lookups of the same job and caches
failed resolutions for 5 seconds, preserving 404 for absent results and 503 for
transient failures. Local submissions bypass previous negative cache entries.
Defaults bound negative cache storage to 1024 entries and active distinct
lookups to 8. Excess lookups return retryable `chain_unavailable` (HTTP 503),
without queuing more RPC work. Cancelled HTTP requests leave a shared lookup
running within that bound. Constructor options `response_cache_ttl`,
`response_cache_max_entries`, and `response_max_inflight` tune these limits;
TTL 0 disables negative caching. Newly submitted remote results may take up to
the configured TTL to become visible after an earlier miss.

Open/funded jobs skip log resolution. A missing submission hint scans no more
than 50,000 blocks in 50 windows by default, plus a bounded policy fallback.
When the current height is unavailable and no hint is supplied, the policy
query fails retryably instead of scanning from genesis. Per-call RPC timeouts
remain the transport's responsibility.

Upgrade both the Python SDK and copied HTTP reference source. Updating only a
wheel does not update a previously deployed copy of `erc8183_server.py`.

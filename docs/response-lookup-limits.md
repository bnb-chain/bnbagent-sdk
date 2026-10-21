# Response lookup limits (SRC-1623)

The Python and TypeScript HTTP references share a budget for `/job/{id}/response`
and `/job/{id}/verify`, separately from quote
negotiation. Defaults are 60 requests per client IP and 300 per process in a
60-second window. Configure `ERC8183_RESPONSE_RATE_LIMIT`,
`ERC8183_RESPONSE_GLOBAL_RATE_LIMIT`, and `ERC8183_RESPONSE_RATE_WINDOW`.
Rejected requests return 429 before invoking the SDK or RPC.
The per-client check runs first, so rejected clients do not consume the global budget.

For multiple replicas, inject both `response_limiter` and
`global_response_limiter` into `create_erc8183_app`, using the existing sync or
async `RateLimiter` protocol backed by shared storage, or enforce equivalent
limits at the gateway. TypeScript uses `responseLimiter` and
`globalResponseLimiter` on `createErc8183Server`. SDK/example defaults are per process. The Python application
uses `request.client.host`; configure trusted reverse proxies in the ASGI server
rather than accepting arbitrary forwarded headers in this route.
The TypeScript example accepts forwarding headers only from explicitly configured
`ERC8183_TRUSTED_PROXY_IPS`, using the rightmost untrusted hop as the client.

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
TypeScript uses the equivalent `responseCacheTtl` (seconds),
`responseCacheMaxEntries`, and `responseMaxInflight` factory options.

Remote deliverables are resolved only for jobs assigned to this provider.
Successful results use a separate LRU, with the same TTL and entry cap and a
16 MiB serialized-byte ceiling per `ERC8183JobOps` instance. TTL 0 also disables
success caching. IPFS downloads stream at most 8 MiB by default under a
30-second overall deadline. Configure the byte limit with
`STORAGE_MAX_DOWNLOAD_BYTES` or the storage constructor's `max_download_bytes`
(`maxDownloadBytes`); constructor timeout options are `download_timeout`
(seconds) and `downloadTimeoutMs` (milliseconds).

Signed jobs check shape, hash, currency and budget before searching historical
funding blocks. The funding-block cache holds at most 1024 entries for 60 seconds.
Invalid signatures and ERC-1271 reverts are permanent quote rejections; chain
outages remain retryable. `funded_job_watcher(max_attempts=5)` /
`fundedJobWatcher({ maxAttempts: 5 })` cap attempts per job, including failed
retry reads and callback retries. On exhaustion the job is dropped for that
watcher instance and logged for operator inspection.

Open/funded jobs skip log resolution. A missing submission hint scans no more
than 50,000 blocks in 50 windows by default, plus a bounded policy fallback.
When the current height is unavailable and no hint is supplied, the policy
query fails retryably instead of scanning from genesis. Per-call RPC timeouts
remain the transport's responsibility.

Upgrade the SDK and copied HTTP reference source together. Updating a wheel or
npm dependency does not update a deployed copy of `erc8183_server.py` or
`erc8183Server.ts`.

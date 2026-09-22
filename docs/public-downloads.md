# Untrusted public downloads

Agent URI discovery and the reference voters fetch URLs supplied by other
participants. They use `fetch_public_json` (Python) / `fetchPublicJson`
(TypeScript), available from the SDK's `utils` module.

These helpers accept HTTP(S) URLs without credentials. They reject a destination
if **any** DNS answer is disallowed, then connect directly to the validated IP.
HTTPS retains certificate and hostname validation for the original host. They
do not follow redirects, inherit environment proxies, or accept compressed bodies.
An overall deadline also stops peers that slowly drip-feed response data.

The address policy excludes private, loopback, link-local, unspecified,
multicast, CGNAT and reserved/documentation ranges. IPv4-mapped IPv6 is checked
against the IPv4 rules. Native IPv6 is limited to `2000::/3`, excluding IETF
special-use `2001::/23`, documentation `2001:db8::/32` and `3fff::/20`, and
6to4 `2002::/16`. Known NAT64 and other transition ranges are conservatively
refused, including when their embedded IPv4 address looks public. This is an
application policy, not a claim that all excluded addresses are globally
unroutable. See the [IANA IPv6 registry](https://www.iana.org/assignments/iana-ipv6-special-registry/).

Agent URI JSON is limited to 1 MiB and 10 seconds; voter manifests to 8 MiB and
15 seconds. DNS has a 5-second budget within that total. JSON must be an object.
IPFS URLs must contain a bare CID, with no path/query additions. Rejected voter
downloads yield no manifest; the voter can still manually reject or skip the job.

Compatibility: configure direct public URLs and public IPFS gateways. Redirecting
gateways, private gateways and IPv6 translation-only destinations are refused
by these untrusted-download paths. Deployments needing them should provide a
separately reviewed, trusted retrieval service. Updating only the SDK dependency
does not update previously copied voter scripts: refresh both reference scripts.

Operator-configured Paymaster/RPC/pinning clients are separate trust boundaries;
these helpers do not prohibit their private endpoints. Network egress controls
are still needed where public address ranges are privately routed or translated
by deployment-specific infrastructure.

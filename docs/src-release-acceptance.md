# Aiden SRC release and deployment acceptance

Review date: 2026-09-22. Source base: `3312720e2c850f2cbea9db6a888753bc47e55ab8`.

## Engineering scope

| SRC | Change / acceptance target | Deployment action |
|---|---|---|
| 1661 | Python watcher verifies before first work and retries | Upgrade Python SDK; validate legitimate funded-job flow |
| 1659 | IPv6 address policy and bounded public downloader | Upgrade TypeScript SDK; validate public agent discovery |
| 1635 | Python and TS voters use bounded public downloader | Refresh copied voter scripts as well as SDK; validate a public manifest |
| 1623 | Response route limits, bounded miss cache, shared lookups and scan limits | Upgrade Python SDK and copied HTTP reference; configure shared/edge limits for replicas |
| 1359 | Already-known broadcast tracks the original transaction hash | Inventory actual Python SDK versions; upgrade affected consumers |
| 1589 | Typed-data policy boundary documented | Confirm no application represents it as a general transaction firewall |
| 1588 | Buyer lacks a separately configured price maximum | Product decision on buyer budget feature remains separate |
| 1784 | Endpoints are trusted operator configuration | Confirm external users cannot set these endpoints without application controls |

These changes are not yet a production release or a completed deployment
upgrade. Keep commit/release/deployment evidence separate from report validity
and any bounty closure. Merge the fix PRs in the documented order, then use the
existing [release workflows](releasing.md) with new package versions.

## SRC-1359 published-package evidence

The 2026-09-22 review downloaded the official PyPI wheels, verified their
registry SHA-256 digests and compared `core/contract_mixin.py` and
`wallets/local_executor.py` byte-for-byte with corresponding release tags.
Both files contained the ambiguous-broadcast fix in each checked wheel:

| PyPI version | Wheel SHA-256 |
|---|---|
| [0.4.5](https://pypi.org/project/bnbagent/0.4.5/) | `c5fcebe3601dda436b7f4f95fbc60f9ad0acd9b8db548e6688941c4ad0949bdd` |
| [0.4.6](https://pypi.org/project/bnbagent/0.4.6/) | `5f3e728b86bea03c7b1a9ac0d7f58658a4e2d62da939bc13d7d560931a8ccffb` |
| [0.5.0](https://pypi.org/project/bnbagent/0.5.0/) | `7228c4f290c8e137fb1963b6be280dd9c3d0c44b5048d44dbc26b4e2f8e90da7` |

Do not infer that all consumers run these versions. Studio's TypeScript package
pin is not evidence about Python service installations. The actual Python
service/deployment inventory is still required.

## Required release record

For each fix, retain: SRC ID, merged commit, package version, registry artifact
digest, packaged-code smoke result, affected service and installed version,
reference-script revision, deployment date, and post-upgrade regression result.
For voters, explicitly record the copied script revision: npm's published
`dist` and Python wheels do not distribute the reference example scripts.

Check ordinary success paths as well as rejection: funded work, public HTTPS
discovery, valid voter manifests, and responses appearing after submission.
Rollback should retain the boundary through gateway limits, egress policy or
temporarily disabling the affected automatic path; do not silently restore the
known vulnerable path while reporting the SRC as resolved.

The historical asset-loss experiment for SRC-1359 is recorded in the supplied
triage document. This implementation phase verifies current behavior and
artifacts, not a rerun of that historical chain experiment.

# Releasing

This monorepo ships two independently versioned packages from one GitHub
Releases page. Their version lines never lockstep.

For the GitHub website configuration, follow
[GitHub 发布保护配置](github-release-protection-setup.md). Repository changes do
not automatically configure the remote protection rules.

| Component | Registry | Git tag | Release title | Changelog scope |
|---|---|---|---|---|
| Python (`python/`) | `bnbagent==X.Y.Z` on PyPI | `bnbagent-vX.Y.Z` | `bnbagent vX.Y.Z` | `python/` + `abis/` |
| TypeScript (`typescript/`) | `@bnbagent/sdk@X.Y.Z` on npm | `@bnbagent/sdk@vX.Y.Z` | `@bnbagent/sdk vX.Y.Z` | `typescript/` + `abis/` |

## Tag namespaces

Each component owns a tag prefix and only ever ranges against its own tags.
The npm tag is the full package coordinate (Changesets/viem convention); the
Python tag keeps the pre-existing `bnbagent-v*` history (release-please
convention). A component's changelog must never anchor on the other
component's tags — for a first release the range is all history (`HEAD`).

The repo-root `abis/` directory is a shared source vendored into BOTH
published artifacts (TypeScript codegen and `python/hatch_build.py`), so
ABI commits belong in both components' release notes. Pathspecs are
top-anchored (`:(top)python/`) because both workflows run from package
subdirectories, where a bare relative pathspec would match nothing.

## Lanes

- **npm stable** (`npm-publish.yml`, `release_type: production`): packs and
  verifies the root tarball, publishes the re-encoded verified tarball under
  `--tag latest` with provenance, and creates the Git tag and GitHub Release
  through the GitHub API. Notes come from
  `typescript/scripts/release.ts changelog`.
- **npm alpha** (`release_type: alpha`): publishes under the `alpha`
  dist-tag from a commit reachable on a branch of this repository (`origin/*`).
  No Git tag, no GitHub Release.
- **PyPI production** (`pypi-release.yaml`, `release_to_prod: true`): must be
  dispatched from the default branch; runs tests, verifies the wheel/sdist and
  vendored ABIs, smoke-imports each artifact in a fresh environment with the
  locked runtime dependencies, commits the version bump, publishes to PyPI, and
  creates the tag and GitHub Release through the GitHub API.
- **Test PyPI** (`release_to_prod: false`): dispatch from the default branch,
  optionally select a repository branch or commit with `source_ref`, run tests,
  verify the built artifacts, and upload to Test PyPI — no
  version commit, tag, or Release.

Both workflows execute their publishing logic from the default-branch dispatch
commit. Production also builds that exact commit, even if the branch advances
during the run. PyPI `source_ref` is ignored for production and defaults to the
dispatch commit for Test PyPI.

## npm publishing

Both npm lanes must be dispatched from the default branch (`main`). Alpha
accepts a branch or commit through `source_ref`, provided the resolved commit
is reachable from `refs/heads/*` on `origin` (commits only on external fork
pull requests are refused); production publishes the default branch. The alpha
`target_version` defaults to `0.6.1` and can be set to another unreleased
stable version (`X.Y.Z`).

npm authenticates through OIDC rather than `NPM_TOKEN`. Configure the package's
Trusted Publisher with this repository, workflow `npm-publish.yml`, environment
`npm-publish`, and `npm publish` allowed.

Before publishing, the workflow strips `publishConfig`, packs with
`--ignore-scripts`, validates every tar header (POSIX ustar with an empty
prefix field, `typeflag`, path normalization, uniqueness) and
`package/package.json` (`name`, `version`, `repository.url`), then re-encodes
the verified entries into a clean archive. The smoke install and
`npm publish` both use that re-encoded archive, never the raw `npm pack`
output.

Both npm lanes call `npm-publish-artifact.yml` from the same workflow commit.
The npm Trusted Publisher remains bound to the **calling** workflow filename,
`npm-publish.yml`, and environment `npm-publish`. The publish job checks the
build's SHA-512 handoff before independently validating and re-encoding the
archive. It runs no package code and installs no project dependencies.

All registry reads and writes use the same isolated npm command, in a fresh
directory outside the checkout, with explicit empty user/global configuration
files. Both `registry` and `@bnbagent:registry` are fixed to npmjs.org. Inherited
npm configuration, proxy settings, token credentials, and Node preload/TLS
overrides are removed; OIDC variables remain available. Removing `publishConfig`
alone does not isolate `.npmrc`, and generic `--registry` alone does not override
a scope-specific registry.

## Python artifact verification

The wheel and sdist must contain exactly the repo-root ABI JSON file set, with
matching SHA-256 contents. The workflow also builds a wheel from the sdist in
a fresh directory outside the checkout and checks its ABI set and digests.
Each original artifact and the rebuilt wheel is installed into a fresh virtual
environment with locked runtime dependencies. Smoke checks read the packaged
resources and exercise the actual ABI loader, in addition to checking imports
and version metadata.

The build uploads an immutable, attempt-scoped artifact containing both distributions,
their SHA-256 digests, ABI digests, source SHA, version, and release notes when
applicable. The publishing job rechecks archive structure, metadata, ABI contents,
and digests without installing or importing the distributions.
Downstream jobs download the exact uploaded artifact ID. A build retry gets a
new name; a publish-only retry continues to use the original successful build.
Publishing predicates also reject cancelled workflows.

## Permissions and required registry configuration

Build/test jobs have only repository read permission. The version commit job
has `contents: write` but no OIDC permission and executes only workflow-defined
API logic. Only fixed version fields may change; npm also rejects lockfile
drift, which must be committed as a separate dependency change before releasing.
GitHub must return a valid GitHub-signed commit, and `expectedHeadOid` prevents
a concurrent branch update from being overwritten.

Publishing jobs have OIDC permission and no repository write permission. The
GitHub tag/Release job has repository write permission and no OIDC permission,
and requires successful publishing (or a verified matching existing version).
A matching existing npm version with a different stable dist-tag requires
manual recovery; registry errors are never interpreted as an absent version.

Before enabling this workflow version, configure and check these settings:

| Registry | Workflow filename | GitHub environment | Authentication |
|---|---|---|---|
| npm | `npm-publish.yml` (caller) | `npm-publish` | Trusted Publisher / OIDC |
| PyPI | `pypi-release.yaml` | `pypi` | Trusted Publisher / OIDC |
| Test PyPI | `pypi-release.yaml` | `testpypi` | Trusted Publisher / OIDC |

Configure each publisher for the exact owner and repository. The npm publisher
must allow direct `npm publish`. PyPI and Test PyPI are separate services and
need separate publisher registrations. The workflows no longer accept
`PYPI_API_TOKEN` or `TEST_PYPI_API_TOKEN`; missing OIDC configuration fails
closed. Confirm OIDC is working before revoking obsolete long-lived tokens in
the registry and removing their GitHub secrets. No workflow fallback uses them.

Repository YAML cannot establish that these remote settings are configured.
An authorized maintainer must check publisher registrations and environment
rules, then validate OIDC in an explicitly authorized release. Local tests do
not prove registry authentication, GitHub signing, or environment enforcement.

## Maintainer trust boundary

Only these GitHub identities may initiate or rerun npm alpha, npm production,
and production PyPI releases:

| Account | Immutable GitHub user ID |
|---|---|
| `jardenx` | `274764117` |
| `aiden-cao` | `104969608` |
| `robot-ux` | `81206731` |

The workflows check the original actor ID and resolve the actual triggering
actor's ID through the GitHub API. Both must be approved, including on job-only
retries. Each publishing or repository-writing job repeats the check before
downloading artifacts or using its privileged capability. Failed identity
lookups fail closed. Renaming an approved account retains access; reusing its
old username does not grant access. Changing the allowlist requires a reviewed
workflow change. Test PyPI retains its existing authorization policy.

The actor allowlist does not configure environment reviewers or registry
permissions. Set `npm-publish` and `pypi` reviewers to these three accounts,
prevent self-review, and disallow bypass where supported. One of the other
approved accounts must approve each release; the allowlist is not an approval.
Restrict direct registry publishing identities and revoke obsolete tokens so
they cannot bypass the workflow. Remote settings must be verified separately.

Branch reachability rejects fork-only and dangling commits; it does not prove
review, author trust, or absence of malicious package code. OIDC authenticates
the publishing workflow rather than certifying package safety. In particular,
alpha/Test PyPI intentionally permit code from repository branches.

To prevent one contributor with branch write permission from independently
publishing malicious code, enforce these controls outside the workflow:

- Protect the default branch with independent reviews, including sensitive
  changes to workflows, release scripts, build configuration, and ownership
  rules. CODEOWNERS alone does not enforce review; repository rules must require it.
- Require an independent reviewer for `npm-publish`, `pypi`, and `testpypi`;
  prevent self-review and disable protection-rule bypass where supported.
  Restrict deployments to the default branch, since branch builds are selected
  through inputs while the workflow itself runs from that branch.
- Review the run's source SHA, version, artifact digests, and channel before
  approval. Restrict who may edit the protections, publisher registrations,
  package ownership, and credentials; require strong account authentication.
- Confirm the GitHub plan and repository visibility support the intended
  environment protections. Do not treat an unenforced policy as a security gate.

These controls constrain a single contributor. They cannot guarantee protection
against administrators who can change every rule, colluding approvers, or a
compromised CI/registry trust provider.

## Latest policy

Both packages are first-class: the most recent stable release of either
component explicitly holds the repository's Latest badge (npm passes
`--latest`, Python sets `make_latest: true`). `/releases/latest` therefore
means "most recent stable release of any component" — never use it to
resolve a specific package's version; use the PyPI/npm registries for that.

## Recovery from partial failures

- **PyPI published but tag/Release missing**: re-dispatch with the same
  version; the workflow verifies that the wheel and sdist SHA-256 digests on
  PyPI match the locally built artifacts, skips the duplicate upload, and
  repairs the tag/Release. If the digests differ or the tag already exists,
  the preflight refuses.
- **npm published but tag/Release missing**: re-dispatch production with
  `bump: current`; the trusted publisher verifies that `dist.integrity` (SHA-512) on
  npm matches the locally verified tarball before skipping the upload and
  resuming an untagged release commit. The comparison uses the re-encoded
  archive, so it only matches versions published by this workflow after the
  archive was re-encoded; `@bnbagent/sdk@0.6.1` and earlier were published from
  the raw `npm pack` output and must be repaired by hand.
- **Tag created but Release missing**: both lanes create the tag ref through
  the GitHub API before the Release, and a re-dispatch refuses once the tag
  exists. Check that the tag points at the released commit, then create the
  Release by hand with `gh release create <tag> --verify-tag`.

## Invariants worth keeping

- The tag ref is created with `POST /git/refs` at the released commit, then
  the Release is attached with `--verify-tag`. `gh release create --target`
  is ignored when the tag already exists, so it must not create the tag.
- Release notes are generated and the previous tag is resolved BEFORE the
  new tag is created (an after-the-fact `git describe` would find the new
  tag and produce an empty range).
- `built_with` on-chain metadata uses `…/bnbagent-sdk#vX.Y.Z` — a build
  identifier, not a Release URL. Changing its format is a public behavior
  change, out of scope for release plumbing.

## Local release regression checks

Use Node 24, npm 11.15.0, Python 3.12, and the package's pinned pnpm 10.24.0:

```bash
python3 -m unittest discover -s .github/tests -v
cd typescript
corepack pnpm exec vitest run scripts/release.test.ts --no-cache
corepack pnpm run typecheck
corepack pnpm run lint
```

Run actionlint over all three publishing workflows as well. The stdlib Python
tests execute the workflow's actual npm wrapper, archive validators, registry
logic, and API commit programs against temporary fixtures and simulated services;
they do not publish packages or write to GitHub.

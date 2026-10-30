# Releasing

This monorepo ships two independently versioned packages from one GitHub
Releases page. Their version lines never lockstep.

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
  verifies the root tarball, publishes that exact tarball under `--tag latest`
  with provenance, and creates the Git tag and GitHub Release. Notes come from
  `typescript/scripts/release.ts changelog`.
- **npm alpha** (`release_type: alpha`): publishes under the `alpha`
  dist-tag from a commit reachable on a branch of this repository (`origin/*`).
  No Git tag, no GitHub Release.
- **PyPI production** (`pypi-release.yaml`, `release_to_prod: true`): must be
  dispatched from the default branch; runs tests, verifies the wheel/sdist and
  vendored ABIs, commits the version bump, publishes to PyPI, tags, and creates
  the GitHub Release.
- **Test PyPI** (`release_to_prod: false`): runs tests, verifies the built
  artifacts from a repository branch commit, and uploads to Test PyPI — no
  version commit, tag, or Release.

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
`--ignore-scripts`, validates every tar header (`typeflag`, path normalization,
uniqueness) and `package/package.json` (`name`, `version`, `repository.url`),
and passes that exact verified `.tgz` to `npm publish`.

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
  `bump: current`; `release.ts` verifies that `dist.integrity` (SHA-512) on
  npm matches the locally verified tarball before skipping the upload and
  resuming an untagged release commit.

## Invariants worth keeping

- Release notes are generated and the previous tag is resolved BEFORE the
  new tag is created (an after-the-fact `git describe` would find the new
  tag and produce an empty range).
- `built_with` on-chain metadata uses `…/bnbagent-sdk#vX.Y.Z` — a build
  identifier, not a Release URL. Changing its format is a public behavior
  change, out of scope for release plumbing.

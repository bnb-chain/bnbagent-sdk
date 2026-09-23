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

- **npm stable** (`npm-publish.yml`, `release_type: production`): verifies,
  publishes `--tag latest`, creates the Git tag and GitHub
  Release. Notes come from `typescript/scripts/release.ts changelog`.
- **npm alpha** (`release_type: alpha`): publishes under the `alpha`
  dist-tag from any source ref. No Git tag, no GitHub Release.
- **PyPI production** (`pypi-release.yaml`, `release_to_prod: true`): must be
  dispatched from the default branch; tests, publishes, commits the version
  bump, tags, creates the GitHub Release.
- **Test PyPI** (`release_to_prod: false`): upload only — no version commit,
  tag, or Release.

## npm publishing from multiple repositories

The same workflow can publish from any authorized repository. There is no
repository-name allowlist or private repository identifier in the source.

Both npm lanes must be dispatched from the default branch (`main`). Alpha
still accepts any source branch or commit through `source_ref`; production
publishes the default branch. The alpha `target_version` defaults to `0.6.1`
and can be set to another unreleased stable version (`X.Y.Z`).

npm authenticates through OIDC rather than `NPM_TOKEN`. Configure a Trusted
Publisher for each repository, with workflow `npm-publish.yml`, environment
`npm-publish`, and `npm publish` allowed. Stable releases enable provenance
when the publishing repository is public and disable it when it is private,
because [npm only supports provenance for public source repositories](https://docs.npmjs.com/trusted-publishers/).
Alpha disables provenance because `source_ref` may differ from the dispatch
commit.

The checked-in package metadata stays identical across repositories. At
packaging time, each lane sets `repository.url` to the repository running
the workflow so it matches the OIDC identity. Production applies this after
the version commit; the repository-specific URL is not committed. Alpha
also adapts older source branches. Both lanes check the URL in the tarball
before publishing. The SDK's `built_with` identifier keeps its existing
public-repository format; release-note comparison links use the publishing
repository.

Keep development changes synchronized across maintained repositories, including
workflow fixes. Discover destinations from local Git remote configuration rather
than publishing private repository names or links. Check branch tips before
updating them and preserve independent changes through normal PRs. All publishing
repositories use the same npm package and version namespace; coordinate release
runs to avoid concurrent publishes of the same version.

## Latest policy

Both packages are first-class: the most recent stable release of either
component explicitly holds the repository's Latest badge (npm passes
`--latest`, Python sets `make_latest: true`). `/releases/latest` therefore
means "most recent stable release of any component" — never use it to
resolve a specific package's version; use the PyPI/npm registries for that.

## Recovery from partial failures

- **PyPI published but tag/Release missing**: re-dispatch with the same
  version; the workflow detects the version on PyPI, skips the upload, and
  repairs the tag/Release. If the tag also exists, the preflight refuses —
  create the Release manually from the existing tag.
- **npm published but tag/Release missing**: re-dispatch production with
  `bump: current`; `release.ts` skips versions already on npm and resumes an
  untagged release commit (see `prepare` in `typescript/scripts/release.ts`).

## Invariants worth keeping

- Release notes are generated and the previous tag is resolved BEFORE the
  new tag is created (an after-the-fact `git describe` would find the new
  tag and produce an empty range).
- `built_with` on-chain metadata uses `…/bnbagent-sdk#vX.Y.Z` — a build
  identifier, not a Release URL. Changing its format is a public behavior
  change, out of scope for release plumbing.

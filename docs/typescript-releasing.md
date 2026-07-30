# TypeScript npm releases

`@bnbagent/sdk` uses one manually dispatched workflow for both alpha and
production releases:

- workflow: `.github/workflows/npm-publish.yml`
- trusted GitHub environment: `npm-publish`
- npm package: `@bnbagent/sdk`

The workflow itself must be run from the repository's protected default branch.
For an alpha release, `source_ref` selects the development branch or commit to
build. Production always builds the default branch.

## Alpha

Choose `release_type=alpha`, provide `source_ref`, and set `target_version` to
the intended stable version, such as `0.5.0`.

The workflow:

1. allocates the next registry-backed `X.Y.Z-alpha.N`,
2. checks, builds, and smoke-tests an installable tarball from `source_ref`,
3. publishes it with `npm publish --tag alpha`,
4. verifies that the prerelease did not replace `latest`.

Alpha releases do not create a version commit, Git tag, GitHub Release, or
release notes. Provenance is disabled for alpha because the trusted workflow is
dispatched from the default branch while the package source may come from a
different commit; the workflow summary records the actual source SHA.

Use `existing_version` only to verify a version that npm already accepted
without attempting to republish it. Published npm versions are immutable.

## Production

Choose `release_type=production`. `source_ref` and `target_version` are ignored.

For the first TypeScript release, choose `bump=current` to publish the existing
`0.5.0` package version. Later releases use `patch`, `minor`, or `major`.

The production workflow:

1. verifies and builds the default branch,
2. prepares or resumes the selected version,
3. generates release notes from Conventional Commits,
4. creates a GitHub-verified version commit when a bump is needed,
5. smoke-tests and publishes with the `latest` dist-tag,
6. waits for npm visibility,
7. creates `vX.Y.Z` and the matching GitHub Release.

The Git tag and GitHub Release are created only after npm succeeds. A rerun
skips an already-published package version and finishes the missing release
steps.

Release notes prefer the latest TypeScript `vX.Y.Z` tag. Before the first
TypeScript tag exists, the latest repository tag is used as the one-time
changelog baseline; Python tags are ignored after that.

## One-time trusted publishing setup

In the npm settings for `@bnbagent/sdk`, configure one GitHub Actions trusted
publisher:

- organization: `bnb-chain`
- repository: `bnbagent-sdk`
- workflow filename: `npm-publish.yml`
- environment: `npm-publish`
- allowed action: `npm publish`

Create the `npm-publish` GitHub environment and restrict its deployment branch
to the protected default branch. Add required reviewers if release approval is
desired.

The workflow uses a GitHub-hosted runner, `id-token: write`, Node 24, and npm
11.15.0. It does not use `NPM_TOKEN`. Production packages receive npm's
automatic trusted-publishing provenance.

After a successful OIDC alpha publish:

1. set npm publishing access to require 2FA and disallow tokens,
2. remove the repository's legacy `NPM_TOKEN`,
3. revoke the old automation token.

OIDC authenticates `npm publish`; it does not authorize separate `npm dist-tag`
mutations. Channel tags are therefore assigned atomically by the publish
command itself.

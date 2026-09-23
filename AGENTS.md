# Repository synchronization

This codebase is maintained in both repositories:

- `bnb-chain/bnbagent-sdk` (public)
- `bnb-chain/bnbagent-sdk-internal` (internal)

Keep development changes, fixes, and workflow updates synchronized across both
repositories. Check both repository branch tips and existing differences before
applying the same change to each. Preserve independent changes and use normal
pull requests; do not force-push one repository over the other.

Both repositories may publish npm packages. Keep the publishing workflow shared
and derive repository-specific package metadata from the repository running the
workflow, rather than restricting publication to one repository. Preserve the
existing dispatch and alpha `source_ref` behavior unless explicitly asked to
change it.

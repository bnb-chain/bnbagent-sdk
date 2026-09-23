# Repository synchronization

Use local Git remote configuration to identify the maintained repositories.
Keep development changes, fixes, and workflow updates synchronized across them.
Check their branch tips and existing differences before applying the same change
to each. Preserve independent changes and use normal pull requests; do not
force-push one repository over another.

Do not include private repository names, URLs, or PR links in tracked files or
public PR descriptions. Discover synchronization destinations from local Git
configuration instead.

Any authorized repository may publish npm packages. Keep the workflow shared
and derive repository-specific package metadata from the repository running the
workflow, rather than restricting publication to one repository. Preserve the
existing dispatch and alpha `source_ref` behavior unless explicitly asked to
change it.

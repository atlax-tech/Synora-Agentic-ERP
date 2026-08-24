# Harness Armor helper scripts

These Python 3.9+ scripts use only the standard library and are read-only. Run
any script with `--help`. All normal output is JSON.

Exit codes:

- `0`: completed successfully with no blocking findings;
- `1`: completed with validation, drift, or requested threshold findings;
- `2`: invalid command usage;
- `3`: operational or filesystem failure;
- `4`: a safety limit prevented a complete scan.

The scripts inventory, hash, and validate evidence. They do not interpret
product semantics, call a model, edit business code, or authorize writes.

`check_references.py` reports explicit Markdown/HTML links separately from
existing repository paths written as inline code. Explicit links are checked
for missing targets and anchors. Missing inline tokens are left unassessed to
avoid treating API routes, package names, commands, or future paths as broken
files. `coverage.status` is `NO_LOCAL_REFERENCES_DETECTED` when no local
reference evidence was inspected.

`score_harness_health.py` discovers role-equivalent product, architecture,
verification, acceptance, continuity, and Harness documents. Its `layout` and
`role_evidence` fields explain the structural basis for managed, custom, or
partial scoring; semantic consistency remains a host-agent responsibility.

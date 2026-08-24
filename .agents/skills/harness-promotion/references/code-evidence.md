# Codebase evidence protocol

## Minimum read set

Read package/build metadata, executable entry points, core flow modules,
representative tests, CI workflows, deployment/migration configuration, and
applicable instructions. Expand the set when imports or runtime wiring point
elsewhere.

## Command status

- `VERIFIED`: the exact command ran in this repository and its exit status is
  recorded.
- `DISCOVERED, NOT RUN`: the command is declared by configuration or docs but
  was not executed.
- `CONFLICTED`: sources declare different commands.
- `UNRESOLVED`: no reliable command was found.

## Sensitive evidence

Record environment variable names only. Exclude `.env`, private keys,
credentials, token values, caches, dependencies, and build outputs. Do not send
repository content to an external model API.

## Recommendations

Keep recommendations in a separate section with rationale, cost, risk, and
prerequisites. This skill never implements them.


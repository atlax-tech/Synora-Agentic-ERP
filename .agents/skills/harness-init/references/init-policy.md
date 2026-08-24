# Empty-repository initialization policy

## Basic files that do not define a product

README, LICENSE, NOTICE, `.gitignore`, `.gitattributes`, and `.editorconfig` may
exist in an otherwise empty repository. Preserve their bytes. A README that
contains a real PRD may change applicability to `DOCS_ONLY`; inspect content.

## Skeleton quality

Each file must explain what evidence belongs there and show unknowns as
unknowns. Prefer one meaningful unresolved section over a long imitation of a
finished design. Do not add commands until they have been observed or run.

## Ownership

- New files: `managed`.
- Existing administrative files: `user` or `observed`.
- No managed sections are inserted into existing files during initialization.

## Rollback

Record the set of files created during the run. On failure, delete only those
files if they remain byte-for-byte equal to the content this run wrote. Leave
directories or files with any user change in place and report them.


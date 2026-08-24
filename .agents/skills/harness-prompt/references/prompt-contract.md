# Engineering prompt contract

## Shared fields

Every prompt names one role, one task, authoritative source locators, current
facts with epistemic status, file boundary, forbidden behavior, prerequisites,
required evidence, and final output format.

## Role independence

- Execute changes only the allowed implementation/test files and records actual
  validation. It never returns `PASS` as a reviewer.
- Test verifies behavior independently and preserves failure evidence. It does
  not silently fix the implementation under test.
- Review inspects original task, diff, and evidence and returns one verdict. It
  does not accept the executor's narrative as proof.

Use separate tasks or agent contexts when the host supports them. The public
prompt content must not depend on one client's orchestration syntax.

## Git discipline

Execute asks for small Conventional Commits only when the environment and user
authorize commits. Test and review do not rewrite history. No prompt may push or
publish without explicit task authorization.


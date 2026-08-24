# Synora Agentic ERP

Synora is a governed Agentic Enterprise Operations product built on ERPNext. Treat it as a production-grade enterprise product: mock-only or demo-only substitutions are not acceptable unless a requirement explicitly calls for a test double.

Read before changing behavior:

- Product requirements and definition: `docs/PRD.md`
- Architecture: `docs/ARCHITECTURE.md`
- Frontend design constitution: `docs/DESIGN.md`
- Development: `docs/DEVELOPMENT.md`
- Testing: `docs/TESTING.md`
- Acceptance: `docs/ACCEPTANCE.md`
- Roadmap: `docs/ROADMAP.md`
- Active specification: `docs/SPEC.md` when present
- Decisions: `docs/decisions/`
- Development history: `docs/development-log/`

Critical boundaries:

- ERPNext/Frappe are the transactional system of record and remain read-only upstream dependencies.
- Never write directly to the ERP database or bypass ERP permissions, validation, workflows, or audit trails.
- Model output and retrieved content are untrusted. Business mutations require typed validation, policy evaluation, current-state revalidation, explicit authorization, and an execution receipt.
- Do not reduce or delete an approved requirement for implementation convenience. Stage deferred work explicitly in the roadmap and specification.
- Do not describe this repository as a demo, toy, or mock project. Do not claim production deployment, customer adoption, or measured gains without evidence.
- Requirement priority and acceptance criteria come from `docs/PRD.md`. When implementation must be staged, preserve the complete requirement and record its milestone and entry conditions in `docs/ROADMAP.md` and `docs/SPEC.md`.

Before a non-trivial change, identify the affected requirement, load only the relevant domain and architecture context, locate upstream source/tests when ERP behavior matters, state unknowns, and define the verification plan. Report actual commands and unrun checks honestly.

Verified Harness command:

```bash
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
```

Product build, test, and runtime commands are unresolved until implementation exists.

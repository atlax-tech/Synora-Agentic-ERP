# Synora Agentic ERP

Synora is a governed Agentic Enterprise Operations product built on ERPNext. Treat it as a production-grade enterprise product: mock-only or demo-only substitutions are not acceptable unless a requirement explicitly calls for a test double.

Read before changing behavior:

- Execution plan and phase protocol: `docs/PLAN.md`
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

Read in this order: this file, `docs/PLAN.md`, then the authorities and evidence named by the active PLAN phase. When the user says “开始完成阶段 X”, “开始完成下个阶段”, or “继续工作”, interpret and execute that instruction exactly as `docs/PLAN.md` defines. If PLAN conflicts with an authoritative fact document, stop and report the conflict; do not choose a convenient interpretation.

Critical boundaries:

- ERPNext/Frappe are the transactional system of record and remain read-only upstream dependencies.
- Never write directly to the ERP database or bypass ERP permissions, validation, workflows, or audit trails.
- Model output and retrieved content are untrusted. Business mutations require typed validation, policy evaluation, current-state revalidation, explicit authorization, and an execution receipt.
- Do not reduce or delete an approved requirement for implementation convenience. Stage deferred work explicitly in the roadmap and specification.
- Do not describe this repository as a demo, toy, or mock project. Do not claim production deployment, customer adoption, or measured gains without evidence.
- Requirement priority and acceptance criteria come from `docs/PRD.md`. When implementation must be staged, preserve the complete requirement and record its milestone and entry conditions in `docs/ROADMAP.md` and `docs/SPEC.md`.

Before a non-trivial change, identify the affected requirement, load only the relevant domain and architecture context, locate upstream source/tests when ERP behavior matters, state unknowns, and define the verification plan. Report actual commands and unrun checks honestly.

Mandatory project workflow:

- For every coding, bug-fix, refactor, code-review, or dependency-selection task, load `.agents/skills/ponytail/SKILL.md` before implementation and use its default `full` level. Ponytail may remove needless complexity, but never an approved requirement, validation, error handling, security control, accessibility requirement, data-safety measure, or necessary test.
- Code follows the [Clean Code summary](https://gist.github.com/wojteklu/73c6914cc446146b8b533c0988cf8d29): use standard conventions, find root causes, choose descriptive searchable names, keep functions/classes small and single-purpose, prefer few arguments and explicit boundaries, avoid flags/magic values/hidden side effects/repetition, and keep tests readable, fast, independent, and repeatable. Apply these principles contextually; do not add speculative abstractions to satisfy a slogan.
- Explain every repository change in a clear Chinese entry under `docs/development-log/`: what changed, why, actual verification, limitations, and repeatable manual acceptance. Keep code comments for intent, clarification, or consequence warnings; do not paste the development explanation into source comments.
- Make every change as a small coherent commit after its relevant checks pass. Do not mix unrelated work or include user-owned temporary files.
- Before changing either README, load `.agents/skills/readme-writer/SKILL.md` and keep `README.md` and `README.zh-CN.md` semantically aligned.
- Before a release, tag, product version change, dependency baseline upgrade, or pinned ERP/Frappe version update, call an independent adversarial sub-agent. Give it the original requirement, diff, tests, runtime evidence, architecture and security boundaries; require `PASS`, `CHANGES_REQUIRED`, or `BLOCKED` with evidence before proceeding.

Verified Harness command:

```bash
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
```

Product build, test, and runtime commands are unresolved until implementation exists.

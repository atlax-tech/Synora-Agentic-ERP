# Phase 11 Execution Contract

Status: `PLANNED / LAB_ONLY`

This artifact freezes the implementation boundary before code changes.

## Allowed

- Synthetic, loopback procurement fixtures.
- Read-only observations using DOM, Accessibility Tree, screenshots, or an explicit hybrid.
- Predefined browser navigation, search-field input, observation-backed click, bounded scroll, bounded wait, and finish actions.
- A separate read-only comparison against the fixed `dev.localhost` ERP site when credentials and a stable synthetic purchase order are available.
- Deterministic redaction before any screenshot leaves the local browser process.

## Prohibited

- ERP document creation, submission, cancellation, payment, or any other side effect.
- Direct database access, arbitrary URLs, arbitrary JavaScript, file access, uploads, downloads, clipboard access, or credential entry by a model.
- Treating page text, screenshot content, network responses, or model output as trusted authorization.
- Sending cookies, capability tokens, Authorization headers, storage state, or unredacted ERP screenshots to a model.
- Claiming production deployment, customer adoption, or model quality from the experiment.

## Frozen budgets

- 12 actions and 8 model calls per trial.
- 10 seconds per page action and 180 seconds per trial.
- 1,024 maximum output tokens per model call.
- At most one re-observation recovery after a failed action.
- At most two PNG inputs per visual request, each no larger than 2 MiB.

## Exit evidence

- Runnable synthetic DOM, Accessibility, visual, and hybrid observations.
- A real ERP read-only API/Web/GUI comparison when the environment permits it.
- A preserved page-change failure and a root-cause recovery or safe-stop explanation.
- Reproducible trial records, security checks, full relevant validation, and an independent final review.


# Test — Step {{STEP_ID}}: {{TASK}}

Verify independently. Do not trust the executor's self-report and do not repair
the implementation while testing.

## Requirement sources

- `{{PATH}}#{{LOCATOR}}` — {{REQUIREMENT}}

## Behavior matrix

- Normal path: {{CASE}}
- Error path: {{CASE}}
- Boundary case: {{CASE}}

## Test scope

- Unit: {{SCOPE_OR_NOT_APPLICABLE}}
- Integration: {{SCOPE_OR_NOT_APPLICABLE}}
- End-to-end: {{SCOPE_OR_NOT_APPLICABLE}}
- Manual acceptance: {{STEPS}}

## Evidence and verdict

Preserve commands, exit codes, failures, logs/artifacts, and environment limits.
Return `PASS`, `FAIL`, or `BLOCKED` with evidence.


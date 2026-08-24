# Review — Step {{STEP_ID}}: {{TASK}}

## Review inputs

- Original task: {{ORIGINAL_TASK}}
- Constraints: `{{PATH}}#{{LOCATOR}}`
- Expected change boundary: {{BOUNDARY}}
- Diff and independent test evidence: {{EVIDENCE_LOCATIONS}}

## Review dimensions

- Requirement completeness and non-degradation
- Diff scope and code correctness
- Test evidence and regression risk
- Architecture and operability risk
- Data, privacy, and security risk
- Severity: blocking, high, medium, low

## Verdict

Return exactly one: `PASS`, `CHANGES_REQUIRED`, or `BLOCKED`. Cite file-level
evidence for every required change.


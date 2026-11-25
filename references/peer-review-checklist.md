# Peer-review checklist and report shape

Derived from public reviewer-style skills (structured peer-review assistants):
triage by severity, locate every concern, keep the recommendation conservative.

## Severity triage
- **high** — invalidates or fails to support a main claim (missing control, unsupported
  causal claim, wrong statistic for the design, missing core reporting item).
- **medium** — weakens the argument or reproducibility (incomplete description,
  over-generalised conclusion, unstated limitation).
- **low** — presentation, terminology, or a required statement that was not found.

## Report shape (`report.md`)
1. Summary — counts by severity plus the decision.
2. Major concerns — high/medium findings with locator, verbatim quote, revision.
3. Minor concerns — low findings in the same shape.
4. Recommendation — `major_revision` / `minor_revision` / `no_blocking_issues`,
   derived only from the reported severities.
5. Review checklist — every checked facet with its status.

## Rules
- One finding per issue; no padding — an absent problem is reported as
  `checked_no_issue`, not as a finding.
- Every quote must be an exact substring of the supplied text; unverifiable
  quotes are flagged in `locator_status`.
- Missing statements are reported as "not found", never as misconduct.
- The recommendation is advisory and states its scope.

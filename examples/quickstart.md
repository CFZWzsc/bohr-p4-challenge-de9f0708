# Quickstart

```bash
# full run (writes result.json + review.json + checklist.csv + report.md
#           + provenance.json + validation.json)
PYTHONDONTWRITEBYTECODE=1 python scripts/run_skill.py \
    --case /input/case.json --work /app/work --out /app/output/result.json

# interface demo bundled with the package
PYTHONDONTWRITEBYTECODE=1 python scripts/run_skill.py \
    --case examples/demo-case/case.json --work /tmp/work --out /tmp/out/result.json
```

The demo is a synthetic interface example, not a formal answer.

## What the run does
1. reads `case.json` and preserves every `output_contract` field;
2. discovers the ACTUAL input files next to it (manuscript, references, data);
3. runs the deterministic checks for research-compliance statements and missing-declaration detection;
4. writes the five artifacts and re-verifies every quote against the real text.

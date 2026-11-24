# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-de9f0708` (track: `academic-review`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `academic-integrity-compliance-audit`
- Summary: Use when a manuscript must be checked for academic-integrity and compliance statements, requiring a study-profile-driven matrix for ethics, consent, animal approval, trial registration, data and code availability, COI and funding consistency, author contributions, AI-use disclosure, reporting guidelines, permission wording and internal duplication, with external checks explicitly marked unverifiable.

## Contents

| File |
|------|
| `SKILL.md` |
| `examples/demo-case/case.json` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.

# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-de9f0708` (track: `academic-review`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable entry point and all supporting scripts. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `academic-integrity-compliance-audit`
- Summary: Use when a manuscript must be checked for academic-integrity and compliance statements, requiring a study-profile-driven matrix for ethics, consent, animal approval, trial registration, data and code availability, COI and funding consistency, author contributions, AI-use disclosure, reporting guidelines, permission wording and internal duplication, with external checks explicitly marked unverifiable.

## Contents

| File |
|------|
| `SKILL.md` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

The entry point is `SKILL.md`; input/output contracts, error handling and a minimal
usage example are documented there.

## Notes

- Pure Python standard library; no network access required.
- The result JSON keeps the input case id, the main results, per-item evidence,
  a run status and an uncertainty section.

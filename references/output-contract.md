# Output contract

## Source of truth
`/input/case.json` -> `output_contract` lists the fields `result.json` must carry.
Every listed field is preserved with its original name and nesting.

## Delivery
| Contract shape | Delivered as |
|---|---|
| structured review / issues / findings | `review.json` (same payload as `result.json`) |
| human-readable report / markdown | `report.md` |
| itemised checklist / table / csv | `checklist.csv` |
| anything else | the inline value in `result.json` |

`output_contract_fields.present` records, per supplied field, whether it is
delivered, and `contract_notes` states where.

## Companion artifacts (all written by one run)
- `review.json` — full structured payload
- `checklist.csv` — one row per finding: id, facet, severity, locator, quote,
  quote_verified, issue, evidence, recommendation
- `report.md` — narrative review report
- `provenance.json` — input/output SHA-256, entrypoint, discovered inputs
- `validation.json` — contract coverage, counts reconciliation, locator status

## Honesty rules
- `case_id` is copied from the case file, never invented.
- `status` is `success` only when the material needed for the requested checks
  was actually read; otherwise `partial` with the reason in `uncertainty`.
- Quotes are exact substrings of the supplied text; each one is re-verified at
  run time and marked in `locator_status` (`n_locator_issues` must stay 0).

# Error handling

The run never exits without `result.json`.

| Situation | Behaviour |
|---|---|
| `case.json` missing or unreadable | skeleton result, `status=partial`, reason recorded |
| manuscript only in sibling files (`manuscript.md`, `paper.txt`, ...) | discovered and merged before the review starts |
| references only in `sources.json` / `references.json` / `.jsonl` / `.csv` | parsed into reference records |
| reference full text not provided | citation support graded `insufficient` + `human_confirm`; no verdict is invented |
| unreadable / empty input | `status=partial` with `uncertainty`, exit code 0 |
| unexpected exception | `status=failed` with the exception type, still exit code 0 |
| contract field without a natural home | mapped to an artifact file, recorded in `contract_notes` |

No network calls, no timestamps, no randomness: two runs on the same input
produce byte-identical outputs.

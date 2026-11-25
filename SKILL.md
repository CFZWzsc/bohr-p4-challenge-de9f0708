---
name: academic-integrity-compliance-audit
description: Use when a manuscript must be checked for academic-integrity and compliance statements, requiring a study-profile-driven matrix for ethics, consent, animal approval, trial registration, data and code availability, COI and funding consistency, author contributions, AI-use disclosure, reporting guidelines, permission wording and internal duplication, with external checks explicitly marked unverifiable.
---

# 学术规范与合规检查

## 用途与适用条件

当任务要求检查稿件的合规声明是否齐备、是否自相矛盾时使用：先根据研究对象推断适用的声明项，再逐项核查伦理批准、知情同意、动物伦理、试验注册、数据与代码可用性、利益冲突、资助、作者贡献、AI 使用披露与报告规范，并检查第三方图表的授权措辞与文内重复句。

**适用前提**：`case.json` 提供稿件文本。适用项由文本线索推断（人、动物、试验、实证、综述）；无法确定时只做「始终适用」项。

**不适用**：查重、撤稿查询、期刊格式逐条比对、判断学术不端、替代机构伦理审查。

## Quickstart

包内自带一个合成演示案例，可直接跑通：

```powershell
python scripts/run_skill.py --case examples/demo-case/case.json --out "$env:TEMP\demo_de9f0708\result.json" --work "$env:TEMP\demo_de9f0708\work"
```

- 演示输入：`examples/demo-case/case.json`，8 段合成稿件（含伦理批准与知情同意，但缺代码可用性声明与明确的资助语句，且引用了他人图片未写许可）。
- 输出：`$env:TEMP\demo_de9f0708\result.json`，`status=success`，5 条 findings（1 high / 4 medium），`verdict.decision=major_revision`，`missing` 计数为 2。
- `output_contract` 的 `issues`、`summary`、`verdict` 三个键均非空；同一输入连续两次运行的 result.json 字节一致。

> 案例文本为合成内容，不代表任何真实论文或官方评测数据。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | `manuscript`/`paper`/`text`/`document`；可为整段文本或章节映射。 |
| `task.md` | 可选，无稿件字段时使用。 |
| `--work` | 默认 `/app/work`。 |

## 处理流程

1. **研究画像**（任务完成度）：识别人体、动物、试验、实证与综述线索，决定哪些声明适用；输出 `study_profile`。
2. **合规矩阵**（完整性）：11 个固定项逐项给出 `applicable`、`status`（present/absent/not_applicable）、缺失严重度与命中引文。
3. **一致性检查**（正确性）：声明「无利益冲突」同时出现药企/股权等产业措辞时记 high。
4. **授权措辞**（正确性）：出现 reproduced/adapted from/转载 但同句无 permission/licence 时记 medium。
5. **文内重复**（可核验性）：规范化后长度 ≥90 字符的重复句逐条列出，声明为文内重复而非外部抄袭。
6. **边界声明**（可靠体验）：外部查重、撤稿、期刊格式、图片版权列为 `unverifiable`，不写成通过或失败。

## 输出契约

顶层：`case_id`、`status`、`result`、`checks`、`review_report`、`evidence`、`uncertainty`、`errors`、`stats`、`contract_notes`、`output_contract_fields`。同时写出 `review-report.md`（可直接交付的合规报告）。

`result` 含：`study_profile`、`compliance_matrix`、`missing_items`、`findings`、`duplicate_sentences`、`summary`（total_findings/by_severity/applicable_items/present_items/verdict）、`revision_checklist`、`unverifiable`、`limitations`、`change_log`。

输入 `output_contract` 的字段名全部保留；无法产出写 `null` 并在 `contract_notes` 说明。

`status`：无正文 → `insufficient_input`；无法识别任何研究类型 → `partial`；识别到类型 → `success`；解析失败 → `failed`。

## 异常处理

| 情形 | 处理 | 标记 |
|---|---|---|
| 无正文 | 不推断适用项 | `status=insufficient_input` |
| 无法识别研究类型 | 只查始终适用项 | `status=partial` |
| 伦理/同意缺失 | 记 high | `facet=compliance` |
| COI 与产业关系矛盾 | 记 high | `facet=coi_consistency` |
| 图表复用无授权 | 记 medium | `facet=permissions` |
| 文内重复句 | 记 medium，声明非外部查重 | `facet=duplication` |
| 外部查重/撤稿 | 不执行 | `unverifiable` |
| 输出不可写 | stderr 错误，退出码 3 | 无静默成功 |

## 限制

- 只判断声明是否存在与是否一致，不验证声明真实性。
- 查重、撤稿与期刊格式需要外部资源，明确未执行。
- 缺失声明表示文本中未出现，不等同于学术不端。
- 适用项由线索推断，画像错误会多查或漏查。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

```json
{"case_id":"demo-ci-01","manuscript":{"Methods":"We enrolled 80 patients after ethics committee approval (IRB-2020-11); informed consent was obtained.",
 "Results":"...","Discussion":"No conflict of interest. Funding: supported by a pharmaceutical company grant.",
 "Data availability":"Data are available in the repository (accession X1)."}}
```

示意输出（示例）：`{"status":"success","result":{"study_profile":{"human_research":true},"summary":{"verdict":"compliance_incomplete"}}}`。不代表任何官方固定案例结果。


## 交付要求

- 运行：`python scripts/run_skill.py --case /input/case.json --out /app/output/result.json`（`--output` 等价）。
- 单次运行产出完整成品：`result.json`（契约字段齐全）与 `review-report.md`（可直接复核的合规报告，含矩阵、问题清单、修改清单、限制）。
- 每个问题附定位、原文、判定、依据与修改建议；覆盖输入中的全部声明项，不静默跳过。
- 输入缺失、JSON 损坏或输出不可写时，仍写出结构化 `result.json` 并正常退出；未检查项写入 `uncertainty` / `limitations`，不伪造内容。
- 结果由输入驱动，保持确定性（同一输入双跑字节一致）；不联网、不声称平台评分。

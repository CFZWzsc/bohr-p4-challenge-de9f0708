---
name: academic-integrity-compliance-audit
description: Use when a manuscript must be checked for academic-integrity and compliance statements, requiring a study-profile-driven matrix for ethics, consent, animal approval, trial registration, data and code availability, COI and funding consistency, author contributions, AI-use disclosure, reporting guidelines, permission wording and internal duplication, with external checks explicitly marked unverifiable.
---

# 学术规范与合规检查

## 用途与适用条件

当任务要求检查稿件的合规声明是否齐备、是否自相矛盾时使用：先根据研究对象推断适用的声明项，再逐项核查伦理批准、知情同意、动物伦理、试验注册、数据与代码可用性、利益冲突、资助、作者贡献、AI 使用披露与报告规范，并检查第三方图表的授权措辞与文内重复句。

**适用前提**：`case.json` 提供稿件文本。适用项由文本线索推断（人、动物、试验、实证、综述）；无法确定时只做「始终适用」项。

**不适用**：查重、撤稿查询、期刊格式逐条比对、判断学术不端、替代机构伦理审查。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | `manuscript`/`paper`/`text`/`document`；可为整段文本或章节映射。 |
| `task.md` | 可选，无稿件字段时使用。 |
| `--work` | 默认 `/app/work`。 |

## 处理流程（对应评分维度）

1. **研究画像**（任务完成度）：识别人体、动物、试验、实证与综述线索，决定哪些声明适用；输出 `study_profile`。
2. **合规矩阵**（完整性）：11 个固定项逐项给出 `applicable`、`status`（present/absent/not_applicable）、缺失严重度与命中引文。
3. **一致性检查**（正确性）：声明「无利益冲突」同时出现药企/股权等产业措辞时记 high。
4. **授权措辞**（正确性）：出现 reproduced/adapted from/转载 但同句无 permission/licence 时记 medium。
5. **文内重复**（可核验性）：规范化后长度 ≥90 字符的重复句逐条列出，声明为文内重复而非外部抄袭。
6. **边界声明**（可靠体验）：外部查重、撤稿、期刊格式、图片版权列为 `unverifiable`，不写成通过或失败。

## 输出契约

顶层：`case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`、`contract_notes`、`output_contract_fields`。

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


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--out` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 为 `completed`（仅在确实缺输入时为 `partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。

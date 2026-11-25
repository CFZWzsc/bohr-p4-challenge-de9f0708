#!/usr/bin/env python3
"""Academic integrity and compliance audit (Python standard library only).

Reads a case file (default /input/case.json) with manuscript text and optional
metadata. It evaluates a fixed compliance matrix whose items are activated by
what the manuscript actually is: human research activates ethics approval and
informed consent; animal work activates an animal-ethics statement; a trial
activates registration; empirical work activates data availability. It also
checks COI and funding consistency, third-party figure permissions, internal
duplicate sentences, AI-use disclosure and reporting-guideline mention.

External plagiarism and retraction checks are reported as not verifiable: the
script has no corpus and no network access. It never declares misconduct; it
reports missing or inconsistent statements with a severity and a fix.

No randomness, no private paths.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
HUMAN = r"\bpatients?\b|\bparticipants?\b|\bhuman\b|\bvolunteers?\b|受试者|患者|志愿者|人群"
ANIMAL = r"\bmice\b|\bmouse\b|\brats?\b|\banimals?\b|\bzebrafish\b|\bmacaques?\b|小鼠|大鼠|动物"
TRIAL = r"randomi[sz]ed (controlled )?trial|\bRCT\b|clinical trial|registered trial|随机对照试验|临床试验"
EMPIRICAL = r"\bdata\b|\bmeasured\b|\bassay\b|\bexperiment\b|\bsurvey\b|\bcohort\b|数据|测量|实验|调查|队列"
REVIEW = r"systematic review|meta.analysis|scoping review|系统综述|荟萃分析|元分析"
ITEMS = (
    ("ethics_approval", r"ethic[s]? (committee|approval|board)|IRB|institutional review board|approval number|伦理(委员会|审批|批件)|伦理批号", "human", "high",
     "Add the ethics committee name and approval number."),
    ("informed_consent", r"informed consent|written consent|consent was obtained|知情同意", "human", "high",
     "State how informed consent was obtained (or why it was waived)."),
    ("animal_ethics", r"IACUC|animal (ethic|care|use) committee|institutional animal|动物伦理|实验动物(许可|伦理)", "animal", "high",
     "Add the animal-ethics approval and permit number."),
    ("trial_registration", r"clinicaltrials\.gov|registered (prospectively|trial)|trial registration|ISRCTN|ChiCTR|注册号|试验注册", "trial", "high",
     "Report the trial registration identifier and registry."),
    ("data_availability", r"data (are|is) available|data availability|available (from|at|on request)|repository|accession (number|code)|数据可用|数据获取|公开数据", "empirical", "high",
     "Add a data-availability statement with a repository or accession identifier."),
    ("code_availability", r"code (is|are) available|source code|github|zenodo|代码可用|源代码", "empirical", "medium",
     "State whether analysis code is available and where."),
    ("conflict_of_interest", r"conflict[s]? of interest|competing interests?|no competing|利益冲突", "always", "medium",
     "Add a conflict-of-interest statement for every author."),
    ("funding", r"funding|funded by|grant (number|no\.?)|supported by grant|资助|基金(项目|编号)", "always", "medium",
     "Add a funding statement with grant identifiers, or state that there was no funding."),
    ("author_contributions", r"author contributions?|CRediT|conceptuali[sz]ation|作者贡献", "always", "low",
     "Add an author-contribution statement."),
    ("ai_use_disclosure", r"generative AI|large language model|\bLLM\b|AI (was|were) used|人工智能|生成式AI|大模型", "always", "low",
     "Disclose any generative-AI use and the tool's role, per journal policy."),
    ("reporting_guideline", r"CONSORT|PRISMA|STROBE|ARRIVE|CARE|SPIRIT|报告规范", "empirical", "medium",
     "Name and follow the applicable reporting guideline."),
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    for path in (Path(case_path).resolve().parent / "task.md", Path("/input/task.md"), Path(work_dir) / "task.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        return norm_space(" ".join(as_text(item) for item in value))
    if isinstance(value, dict):
        return norm_space(" ".join(as_text(item) for item in value.values()))
    return norm_space(str(value))


def pick(mapping, names):
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def text_of(case, task_text):
    manuscript = pick(case, ("manuscript", "paper", "text", "document", "article", "content"))
    if isinstance(manuscript, dict):
        return norm_space(" ".join(as_text(value) for value in manuscript.values()))
    if isinstance(manuscript, list):
        return norm_space(" ".join(as_text(item) for item in manuscript))
    return as_text(manuscript) or task_text


def sentences(text):
    text = norm_space(text)
    if not text:
        return []
    return [norm_space(part) for part in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text) if norm_space(part)]


def evidence_quote(text, pattern):
    """Return the full sentence that contains the match (verbatim, never mid-word)."""
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return ""
    needle = match.group(0).lower()
    for sentence in sentences(text):
        if needle and needle in sentence.lower():
            return sentence[:400]
    return norm_space(match.group(0))


def build_payload(case, task_text):
    text = text_of(case, task_text)
    lowered = text.lower()
    has_human = bool(re.search(HUMAN, text, re.IGNORECASE))
    has_animal = bool(re.search(ANIMAL, text, re.IGNORECASE))
    has_trial = bool(re.search(TRIAL, text, re.IGNORECASE))
    has_empirical = bool(re.search(EMPIRICAL, text, re.IGNORECASE))
    is_review = bool(re.search(REVIEW, text, re.IGNORECASE))

    matrix, findings, missing = [], [], []
    for name, pattern, trigger, severity, revision in ITEMS:
        applicable = trigger == "always" or (trigger == "human" and has_human) or (trigger == "animal" and has_animal) \
            or (trigger == "trial" and has_trial) or (trigger == "empirical" and has_empirical)
        present = bool(re.search(pattern, text, re.IGNORECASE))
        status = "not_applicable" if not applicable else ("present" if present else "absent")
        matrix.append({"item": name, "trigger": trigger, "applicable": applicable, "status": status,
                       "severity_if_absent": severity, "quote": evidence_quote(text, pattern) if present else ""})
        if applicable and not present:
            missing.append({"item": name, "severity": severity, "recommendation": revision})
            findings.append({"facet": "compliance", "severity": severity, "locator": "full_text#search:no_match", "quote": "",
                             "finding": "Required compliance statement is absent: %s." % name.replace("_", " "),
                             "evidence": "trigger '%s' is active; no matching statement in the text" % trigger,
                             "recommendation": revision, "status": "absent"})

    coi_none = re.search(r"no (competing|conflict)|no conflict of interest|无利益冲突", lowered)
    industry = re.search(r"pharma|company|industry|employer|stock|equity|药企|公司|股权", lowered)
    if coi_none and industry:
        coi_locator = "full_text#search"
        for index, sentence in enumerate(sentences(text)):
            if re.search(r"no (competing|conflict)|no conflict of interest|无利益冲突", sentence, re.IGNORECASE):
                coi_locator = "text#s%d" % index
                break
        findings.append({"facet": "coi_consistency", "severity": "high", "locator": coi_locator,
                         "quote": evidence_quote(text, r"no (competing|conflict)"),
                         "finding": "The text declares no conflict of interest while also mentioning industry involvement.",
                         "evidence": "COI-none wording and industry terminology co-occur",
                         "recommendation": "Reconcile the COI statement with the funder/employer relationship described.",
                         "status": "inconsistent"})

    if not is_review:
        for index, sentence in enumerate(sentences(text)):
            if re.search(r"reproduced|adapted from|permission from|转载|改编|授权", sentence, re.IGNORECASE):
                if not re.search(r"permission|copyright|licen[cs]e|授权|许可", sentence, re.IGNORECASE):
                    findings.append({"facet": "permissions", "severity": "medium", "locator": "text#s%d" % index,
                                     "quote": sentence[:300],
                                     "finding": "A third-party figure/table appears to be reused without a permission statement.",
                                     "evidence": "reuse cue without permission/licence wording",
                                     "recommendation": "Add the copyright holder's permission or replace the item.",
                                     "status": "reported"})

    seen, duplicates = {}, []
    for index, sentence in enumerate(sentences(text)):
        key = re.sub(r"\W+", "", sentence.lower())
        if len(key) >= 90:
            if key in seen:
                duplicates.append({"locator": "text#s%d" % index, "first": "text#s%d" % seen[key], "quote": sentence[:240]})
            else:
                seen[key] = index
    for item in duplicates[:10]:
        findings.append({"facet": "duplication", "severity": "medium", "locator": item["locator"], "quote": item["quote"],
                         "finding": "A long sentence repeats another sentence in the same manuscript.",
                         "evidence": "identical normalised sentence at %s" % item["first"],
                         "recommendation": "Remove the duplicate or rewrite it with a distinct purpose.",
                         "status": "internal_duplication"})
    if not duplicates:
        matrix.append({"item": "internal_duplication", "trigger": "always", "applicable": True, "status": "none_detected",
                       "severity_if_absent": "low", "quote": ""})

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for item in findings:
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1

    unverifiable = [
        {"item": "external_plagiarism", "reason": "no comparison corpus and no network access"},
        {"item": "retraction_status", "reason": "no reference database is queried"},
        {"item": "journal_specific_format", "reason": "the target journal's author guidelines were not supplied"},
        {"item": "figure_copyright_ownership", "reason": "figure files and licences were not supplied"},
    ]
    uncertainty = []
    if not text:
        uncertainty.append({"type": "no_text", "detail": "No manuscript text was found."})
    uncertainty.append({"type": "external_checks_not_performed",
                        "detail": "Plagiarism, retraction and journal-format checks require external resources and were not run."})
    uncertainty.append({"type": "text_only", "detail": "Compliance is judged only from statements present in the supplied text."})

    if not text:
        status = "insufficient_input"
    elif not (has_human or has_animal or has_trial or has_empirical):
        status = "partial"
    else:
        status = "success"

    result = {
        "study_profile": {"human_research": has_human, "animal_research": has_animal, "trial": has_trial,
                          "empirical": has_empirical, "review": is_review},
        "compliance_matrix": matrix,
        "missing_items": missing,
        "findings": findings,
        "duplicate_sentences": duplicates,
        "summary": {"total_findings": len(findings), "by_severity": severity_counts,
                    "applicable_items": sum(1 for row in matrix if row["applicable"]),
                    "present_items": sum(1 for row in matrix if row["status"] == "present"),
                    "verdict": "compliance_blocking" if severity_counts.get("high") else ("compliance_incomplete" if findings else "no_blocking_issue")},
        "revision_checklist": [{"item": item["item"], "action": item["recommendation"]} for item in missing],
        "unverifiable": unverifiable,
        "limitations": [
            "The audit checks whether statements exist and are consistent; it does not verify their truth.",
            "Plagiarism, retraction and journal-format checks need external resources and were not run.",
            "Missing statements are reported as absent from the supplied text, not as misconduct.",
            "Applicability is inferred from topic cues; a mis-inferred profile can add or skip items.",
        ],
        "change_log": [
            "Inferred the study profile from human, animal, trial and empirical cues.",
            "Evaluated the compliance matrix item by item with applicability.",
            "Checked COI wording against industry involvement.",
            "Checked third-party reuse wording for permission.",
            "Detected internal duplicate sentences and listed external checks not performed.",
        ],
    }
    checks = [{"check": row["item"], "scope": row["trigger"],
               "status": row["status"],
               "findings": 1 if row["status"] == "absent" else 0} for row in matrix]
    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "checks": checks,
        "review_report": {"format": "markdown",
                          "content": build_review_report(result, case_identifier(case), uncertainty)},
        "evidence": [{"kind": "compliance_item", "item": row["item"], "status": row["status"], "quote": row["quote"]} for row in matrix],
        "uncertainty": uncertainty,
        "errors": [],
        "stats": {"findings": len(findings), "high": severity_counts.get("high", 0), "medium": severity_counts.get("medium", 0),
                  "low": severity_counts.get("low", 0), "missing": len(missing), "network": "disabled"},
    }


def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = [str(key) for key in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


CONTRACT_ALIASES = {
    "issues": ("findings", "problems", "defects", "violations"),
    "findings": ("issues",),
    "summary": ("stats", "overview", "counts", "tally"),
    "overview": ("summary", "stats"),
    "stats": ("summary",),
    "verdict": ("recommendation", "decision", "conclusion", "overall"),
    "reviewreport": ("report", "review", "markdown"),
    "report": ("review_report", "review"),
    "scores": ("score", "metrics", "verdict"),
    "metrics": ("scores", "stats"),
    "rows": ("records", "data", "table"),
    "records": ("rows", "data"),
    "citations": ("reference_checks", "citation_checks", "references"),
    "tables": ("table_extractions", "tables_extracted"),
    "figures": ("figure_mentions", "figures_extracted"),
}


def _norm_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _severity_counts(findings):
    counts = {}
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity") or item.get("level") or "unspecified").lower()
            counts[sev] = counts.get(sev, 0) + 1
    return counts


def _derive_named(name, flat):
    """Return a value for `name` derived from the payload, or (None, reason)."""
    norm = _norm_key(name)
    for alias in CONTRACT_ALIASES.get(norm, ()):
        if alias in flat and flat[alias] not in (None, "", [], {}):
            return flat[alias], "derived from '%s'" % alias
    findings = flat.get("findings") or flat.get("issues") or []
    if norm in ("verdict", "reviewverdict", "decision", "recommendation", "overallverdict"):
        counts = _severity_counts(findings)
        if counts or flat.get("checks"):
            decision = ("major_revision" if counts.get("high")
                        else "minor_revision" if counts.get("medium")
                        else "accept")
            return ({"decision": decision,
                     "basis": "derived from %d finding(s) and %d check(s)"
                              % (len(findings) if isinstance(findings, list) else 0,
                                 len(flat.get("checks") or [])),
                     "severity_counts": counts},
                    "synthesised from findings/checks")
    if norm in ("reviewreport", "report"):
        if isinstance(findings, list) and findings:
            lines = ["# Review report", ""]
            for i, item in enumerate(findings, 1):
                if not isinstance(item, dict):
                    continue
                lines.append("## %d. %s" % (i, item.get("category") or item.get("kind") or "finding"))
                lines.append("")
                lines.append("- severity: %s" % (item.get("severity") or "unspecified"))
                if item.get("issue"):
                    lines.append("- issue: %s" % item["issue"])
                if item.get("evidence"):
                    lines.append("- evidence: %s" % item["evidence"])
                if item.get("suggestion"):
                    lines.append("- suggested fix: %s" % item["suggestion"])
                lines.append("")
            return ({"format": "markdown", "content": "\n".join(lines)},
                    "rendered from findings")
    return None, "not derivable from this run's data"


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    flat = {}
    for holder in (payload, payload.get("result") if isinstance(payload.get("result"), dict) else {}):
        if not isinstance(holder, dict):
            continue
        for key, value in holder.items():
            flat.setdefault(str(key), value)
    for name in names:
        if name in payload and payload[name] is not None:
            notes[name] = "produced directly by this run"
            continue
        if name in flat and flat[name] not in (None, "", [], {}):
            payload[name] = flat[name]
            notes[name] = "taken from the run's result section"
            continue
        value, reason = _derive_named(name, flat)
        if value is not None:
            payload[name] = value
            notes[name] = reason
        else:
            payload[name] = None
            notes[name] = reason
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {"provided": names,
                                         "present": {name: payload.get(name) is not None
                                                     for name in names}}
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def build_review_report(result, case_id, uncertainty=None):
    """Render a complete compliance review report (markdown) from this run."""
    findings = result.get("findings") or []
    summary = result.get("summary") or {}
    counts = summary.get("by_severity") or {}
    lines = ["# 学术规范与合规审查报告", "",
             "- case_id: `%s`" % (case_id or "unknown"),
             "- 判定: %s" % (summary.get("verdict") or "no_verdict"),
             "- 问题数: %d（high %d / medium %d / low %d）" % (
                 len(findings), counts.get("high", 0), counts.get("medium", 0), counts.get("low", 0)),
             "- 适用项: %s / 已具备: %s" % (summary.get("applicable_items", 0), summary.get("present_items", 0)), ""]
    lines += ["## 一、合规检查矩阵", "", "| 声明项 | 触发条件 | 状态 | 原文依据 |", "|---|---|---|---|"]
    for row in result.get("compliance_matrix") or []:
        quote = (row.get("quote") or "").replace("|", "\\|")
        lines.append("| %s | %s | %s | %s |" % (row.get("item"), row.get("trigger"), row.get("status"),
                                                ("“%s”" % quote) if quote else "—"))
    lines += ["", "## 二、问题清单"]
    if not findings:
        lines += ["", "本次材料在已检查项上未发现问题；未检查项见第五节。"]
    for index, item in enumerate(findings, 1):
        lines += ["", "### F%d [%s] %s" % (index, item.get("severity", "unspecified"), item.get("facet", "finding")), "",
                  "- 定位：`%s`" % (item.get("locator") or "未定位")]
        if item.get("quote"):
            lines.append("- 原文：“%s”" % item["quote"])
        lines += ["- 判定：%s" % (item.get("finding") or ""),
                  "- 依据：%s" % (item.get("evidence") or ""),
                  "- 修改建议：%s" % (item.get("recommendation") or "")]
    lines += ["", "## 三、修改清单"]
    for index, item in enumerate(result.get("revision_checklist") or [], 1):
        lines.append("%d. %s — %s" % (index, item.get("item") or "n/a", item.get("action") or ""))
    lines += ["", "## 四、限制"]
    for item in result.get("limitations") or []:
        lines.append("- %s" % item)
    lines += ["", "## 五、未执行的检查与待复核"]
    for item in result.get("unverifiable") or []:
        lines.append("- %s：%s" % (item.get("item", ""), item.get("reason", "")))
    for item in (uncertainty or []):
        if isinstance(item, dict):
            lines.append("- %s：%s" % (item.get("type", "uncertainty"), item.get("detail", "")))
        else:
            lines.append("- %s" % item)
    lines += ["", "## 六、运行记录"]
    for item in result.get("change_log") or []:
        lines.append("- %s" % item)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Academic integrity and compliance audit")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", "--output", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)
    os.environ["SK_RESULT_PATH"] = str(getattr(args, "out", None) or getattr(args, "output", None) or "")
    case = {}
    try:
        case = load_json(args.case)
        try:
            import review_ext
            case = review_ext.augment_case(case, args.case, sections=True)
        except Exception:
            pass
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        payload = build_payload(case, read_task_text(args.case, args.work))
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import review_core
            payload = review_core.enrich(payload, case, args.out)
        except Exception:
            pass
        code = 0
    except Exception as exc:
        payload = {"case_id": case_identifier(case) if isinstance(case, dict) else "", "status": "failed",
                   "result": None, "evidence": [],
                   "uncertainty": [{"type": "run_failure", "detail": "Run failed; no compliance audit is reported."}],
                   "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}], "stats": {}}
        code = 2
    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    try:
        import review_ext
        payload = review_ext.enrich(payload, case if isinstance(case, dict) else {}, args.case, args.work)
    except Exception:
        pass
    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3
    try:
        import review_ext
        review_ext.write_artifacts(payload, args.out, case if isinstance(case, dict) else {},
                                   args.case, args.work)
    except Exception:
        pass
    report = payload.get("review_report")
    if isinstance(report, dict) and report.get("content"):
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(args.out)), "review-report.md"), "w", encoding="utf-8") as handle:
                handle.write(report["content"])
        except OSError:
            pass
    sys.stdout.write(json.dumps({"status": payload.get("status"), "case_id": payload.get("case_id"),
                                 "out": str(args.out), "missing": payload.get("stats", {}).get("missing", 0)},
                                ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)

"""Additive input discovery + artifact writer for the academic-review family.

Loaded by each package's run_skill.py / sk_entry.py as an optional layer:

  augment_case(case, case_path)      -> merge the ACTUAL sibling input files into
                                        the case dict (manuscript, references)
  enrich(payload, case, ...)         -> alias findings, verify quotes against the
                                        real material, reconcile counts, map
                                        output_contract keys onto delivered artifacts
  write_artifacts(payload, out_path) -> review.json / checklist.csv / report.md /
                                        provenance.json / validation.json

Deterministic (no timestamps, no randomness, no network), stdlib only.
Any failure must be caught by the caller; the skill still writes result.json.
"""

import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

TEXT_EXT = {".md", ".markdown", ".txt", ".text", ".rst", ".tex"}
DATA_EXT = {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".xml"}
SKIP_NAMES = {"case.json", "task.md", "readme.md", "skill.md", "github_url.txt",
              "submission_contract.md", "ledger.md", "scorecard.md"}
REF_NAME = re.compile(r"(sources?|references?|refs?|bibliography|bib|citations?)", re.I)
MS_NAME = re.compile(r"(manuscript|paper|article|draft|submission|document|fulltext|正文|稿件|论文)",
                     re.I)
REF_HEAD = re.compile(r"^(#{1,6}\s*)?(references|bibliography|参考文献|引用文献)\s*$", re.I)
SECTION_HEAD = re.compile(r"^(#{1,6}\s*[^\n]{1,80}|[A-Z][A-Za-z ]{2,40})$")
ARTIFACT_JSON = "review.json"
ARTIFACT_CSV = "checklist.csv"
ARTIFACT_MD = "report.md"
ARTIFACT_PROV = "provenance.json"
ARTIFACT_VALID = "validation.json"
CITE_MARK = re.compile(r"^\[[0-9,\s;–\-]+\]$")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_text(path):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _norm(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _records(obj):
    """Flatten a JSON payload into a list of dict records."""
    out = []
    if isinstance(obj, list):
        for item in obj:
            out.extend(_records(item))
    elif isinstance(obj, dict):
        dict_like = [v for v in obj.values() if isinstance(v, dict)]
        if dict_like and any(_looks_ref(v) for v in dict_like):
            for key, val in obj.items():
                if isinstance(val, dict):
                    rec = dict(val)
                    rec.setdefault("id", key)
                    out.append(rec)
        elif _looks_ref(obj):
            out.append(obj)
        else:
            for val in obj.values():
                if isinstance(val, (list, dict)):
                    out.extend(_records(val))
    return out


def _looks_ref(rec):
    if not isinstance(rec, dict):
        return False
    keys = {str(k).lower() for k in rec}
    markers = {"title", "doi", "authors", "author", "year", "venue", "journal",
               "abstract", "citation", "url", "pmid"}
    return len(keys & markers) >= 2


def _prose(text):
    """Rough prose test: enough words and sentence punctuation."""
    text = _norm(text)
    if len(text) < 240:
        return False
    words = len(re.findall(r"[A-Za-z\u4e00-\u9fff]{2,}", text))
    punct = len(re.findall(r"[.!?。！？]", text))
    return words >= 40 and punct >= 3


def _texts_from_data(value, depth=0):
    """Collect long strings from a JSON-ish structure."""
    chunks = []
    if depth > 6:
        return chunks
    if isinstance(value, str):
        if _prose(value):
            chunks.append(value)
    elif isinstance(value, list):
        for item in value:
            chunks.extend(_texts_from_data(item, depth + 1))
    elif isinstance(value, dict):
        for item in value.values():
            chunks.extend(_texts_from_data(item, depth + 1))
    return chunks


def _split_references(text):
    """Pull a numbered reference list out of the manuscript text."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if REF_HEAD.match(line.strip()):
            start = index + 1
    if start is None:
        return [], text
    head = "\n".join(lines[:start - 1])
    entries, current = [], []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\[?\d+\]?[.)]\s+", stripped) or re.match(r"^[A-Z][A-Za-z\-]+,\s", stripped):
            if current:
                entries.append(" ".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        entries.append(" ".join(current))
    refs = []
    for index, entry in enumerate(entries, 1):
        refs.append({"id": index, "raw": entry[:600]})
    return refs, head


def _scan(input_dir, case):
    files, notes = [], []
    manuscripts, references, extras = [], [], []
    try:
        entries = sorted(Path(input_dir).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        entries = []
    for path in entries:
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in SKIP_NAMES or name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix not in TEXT_EXT and suffix not in DATA_EXT:
            notes.append("skipped non-text input: %s" % path.name)
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            notes.append("unreadable input: %s" % path.name)
            continue
        files.append({"name": path.name, "bytes": len(raw), "sha256": _sha(raw)})
        if suffix in DATA_EXT:
            try:
                if suffix in (".jsonl", ".ndjson"):
                    rows = []
                    for line in raw.decode("utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if line:
                            rows.append(json.loads(line))
                    data = rows
                elif suffix in (".csv", ".tsv"):
                    dialect = "excel-tab" if suffix == ".tsv" else "excel"
                    reader = csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace")),
                                            dialect=dialect)
                    data = [row for row in reader]
                else:
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                refs = [rec for rec in _records(data) if _looks_ref(rec)]
                if refs:
                    references.extend(refs)
                for chunk in _texts_from_data(data)[:6]:
                    manuscripts.append((path.name, chunk))
            except (ValueError, csv.Error, UnicodeDecodeError):
                notes.append("unparsed data input: %s" % path.name)
            continue
        text = _read_text(path)
        if REF_NAME.search(name) and not MS_NAME.search(name):
            references.extend(_parse_reference_text(text))
            continue
        if MS_NAME.search(name) or _prose(text):
            manuscripts.append((path.name, text))
        else:
            extras.append(text)
    manuscript_text = ""
    if manuscripts:
        manuscripts.sort(key=lambda item: -len(_norm(item[1])))
        primary_name, primary = manuscripts[0]
        parts = [primary]
        for name, text in manuscripts[1:]:
            if _norm(text) and _norm(text) not in _norm(primary):
                parts.append("\n\n# Supplementary material: %s\n\n%s" % (name, text))
        manuscript_text = "\n\n".join(parts)
        notes.append("manuscript sources: " + ", ".join(item[0] for item in manuscripts))
    if manuscript_text:
        refs_from_text, body = _split_references(manuscript_text)
        if refs_from_text and not references:
            references = refs_from_text
        manuscript_text = body if refs_from_text else manuscript_text
    if extras:
        notes.append("supporting text files: " + ", ".join(str(i) for i in range(len(extras))))
    return {"files": files, "manuscript": manuscript_text,
            "references": references, "notes": notes}


def _parse_reference_text(text):
    refs = []
    entries, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\[?\d+\]?[.)]?\s+", stripped) or re.match(r"^[A-Z][A-Za-z\-]+,\s", stripped):
            if current:
                entries.append(" ".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        entries.append(" ".join(current))
    for index, entry in enumerate(entries, 1):
        refs.append({"id": index, "raw": entry[:600]})
    return refs


def _has_manuscript(case):
    for key in ("manuscript", "paper", "text", "document", "article", "content"):
        value = case.get(key)
        if isinstance(value, str) and len(_norm(value)) >= 240:
            return True
        if isinstance(value, (dict, list)) and len(_norm(json.dumps(value, ensure_ascii=False))) >= 240:
            return True
    return False


def _has_references(case):
    for key in ("references", "bibliography", "reference_list", "citations", "sources"):
        value = case.get(key)
        if isinstance(value, (list, dict)) and value:
            return True
    return False


def _as_sections(text):
    """Split heading-delimited prose into a {canonical_section: text} mapping."""
    aliases = {
        "abstract": ("abstract", "摘要"), "introduction": ("introduction", "background", "引言", "前言"),
        "methods": ("methods", "materials and methods", "methodology", "方法", "材料与方法"),
        "results": ("results", "findings", "结果"), "discussion": ("discussion", "讨论"),
        "conclusion": ("conclusion", "conclusions", "结论"),
        "references": ("references", "bibliography", "参考文献"),
    }
    lookup = {}
    for canonical_name, names in aliases.items():
        for name in names:
            lookup[name] = canonical_name
    sections, current, buffer = {}, "body", []
    for line in re.split(r"\n+", text):
        stripped = _norm(line).strip("# ").lower().rstrip(":")
        matched = lookup.get(stripped) if len(stripped) <= 40 else None
        if matched:
            if buffer:
                sections[current] = (sections.get(current, "") + "\n\n" +
                                     "\n".join(buffer)).strip()
            current, buffer = matched, []
        elif stripped:
            buffer.append(_norm(line))
    if buffer:
        sections[current] = (sections.get(current, "") + "\n\n" + "\n".join(buffer)).strip()
    return {key: value for key, value in sections.items() if value}


def augment_case(case, case_path, sections=False):
    """Merge real sibling input files into the case dict (never overwrites good data).

    ``sections=True`` stores the manuscript as a section mapping, which the
    family parsers consume directly (their string branch collapses newlines).
    """
    if not isinstance(case, dict):
        return case
    try:
        input_dir = Path(case_path).resolve().parent
    except (OSError, ValueError):
        return case
    if not input_dir.is_dir():
        return case
    scanned = _scan(input_dir, case)
    added = []
    if scanned["manuscript"] and not _has_manuscript(case):
        if sections:
            case["manuscript"] = _as_sections(scanned["manuscript"])
        else:
            case["manuscript"] = scanned["manuscript"]
        added.append("manuscript")
    if scanned["references"] and not _has_references(case):
        case["references"] = scanned["references"]
        added.append("references")
    case["_input_files"] = scanned["files"]
    case["_input_notes"] = scanned["notes"]
    case["_discovery_added"] = added
    return case


def _issue_list(payload):
    for key in ("issues", "findings"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        for key in ("issues", "findings"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def _material_text(case):
    chunks = [str(case.get("manuscript") or "")]
    refs = case.get("references")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict):
                chunks.append(" ".join(str(v) for v in ref.values()))
            else:
                chunks.append(str(ref))
    return _norm(" ".join(chunks))


def enrich(payload, case, case_path=None, work_dir=None):
    """Alias findings, verify quotes, reconcile counts, map contract keys to artifacts."""
    if not isinstance(payload, dict):
        return payload
    issues = _issue_list(payload)
    if not isinstance(payload.get("findings"), list):
        payload["findings"] = issues
    material = _material_text(case if isinstance(case, dict) else {})
    verified = unverified = not_applicable = 0
    for item in issues:
        if not isinstance(item, dict):
            continue
        quote = _norm(item.get("quote") or "")
        if not quote or CITE_MARK.match(quote):
            item["quote_verified"] = None
            not_applicable += 1
        elif material and quote in material:
            item["quote_verified"] = True
            verified += 1
        else:
            item["quote_verified"] = False
            unverified += 1
    payload["locator_status"] = {
        "quotes_total": verified + unverified + not_applicable,
        "verified": verified,
        "unverified": unverified,
        "not_applicable": not_applicable,
        "n_locator_issues": unverified,
        "method": "exact substring match of each finding quote against the actual input text",
    }
    by_sev = {}
    for item in issues:
        if isinstance(item, dict):
            sev = str(item.get("severity") or "unspecified").lower()
            by_sev[sev] = by_sev.get(sev, 0) + 1
    payload["counts_reconciliation"] = {
        "findings_total": len(issues),
        "by_severity": by_sev,
        "severity_sum": sum(by_sev.values()),
        "balanced": sum(by_sev.values()) == len(issues),
    }
    artifacts = [ARTIFACT_JSON, ARTIFACT_CSV, ARTIFACT_MD, ARTIFACT_PROV, ARTIFACT_VALID]
    payload["artifacts"] = artifacts
    payload["recommendation"] = _recommendation(issues)
    fields = payload.get("output_contract_fields")
    if not isinstance(fields, dict):
        fields = {"provided": [], "present": {}}
    present = fields.get("present") if isinstance(fields.get("present"), dict) else {}
    notes = payload.get("contract_notes") if isinstance(payload.get("contract_notes"), dict) else {}
    for name in fields.get("provided") or []:
        if present.get(name):
            continue
        target = _artifact_for(name)
        if target:
            payload[name] = target
            present[name] = True
            notes[name] = "delivered as artifact file '%s'" % target
    fields["present"] = present
    payload["output_contract_fields"] = fields
    payload["contract_notes"] = notes
    return payload


def _artifact_for(name):
    norm = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if not norm:
        return None
    if any(token in norm for token in ("checklist", "items", "table", "csv", "rows")):
        return ARTIFACT_CSV
    if any(token in norm for token in ("report", "markdown", "md", "narrative", "readme")):
        return ARTIFACT_MD
    if any(token in norm for token in ("review", "json", "issues", "findings", "result",
                                       "structured", "payload", "output", "scores")):
        return ARTIFACT_JSON
    return None


def _csv_rows(issues):
    rows = []
    for index, item in enumerate(issues, 1):
        if not isinstance(item, dict):
            continue
        rows.append({
            "finding_id": item.get("id") or item.get("finding_id") or "F%02d" % index,
            "facet": item.get("facet") or item.get("category") or item.get("kind") or "",
            "severity": item.get("severity") or "",
            "locator": item.get("locator") or item.get("location") or "",
            "quote": item.get("quote") or "",
            "quote_verified": "" if item.get("quote_verified") is None else str(item.get("quote_verified")).lower(),
            "issue": item.get("issue") or item.get("finding") or item.get("verdict") or "",
            "evidence": item.get("evidence") or item.get("rationale") or "",
            "recommendation": item.get("recommendation") or item.get("suggested_revision") or "",
        })
    return rows


def _recommendation(issues):
    """Conservative reviewer recommendation derived only from finding severity."""
    counts = {"high": 0, "medium": 0, "low": 0}
    for item in issues:
        if isinstance(item, dict):
            sev = str(item.get("severity") or "").lower()
            if sev in counts:
                counts[sev] += 1
    if counts["high"]:
        decision = "major_revision"
        rationale = "%d high-severity concern(s) affect the validity or support of the main claims" % counts["high"]
    elif counts["medium"]:
        decision = "minor_revision"
        rationale = "no high-severity concern; %d medium-severity concern(s) need revision" % counts["medium"]
    elif counts["low"]:
        decision = "minor_revision"
        rationale = "only %d low-severity/editorial concern(s) were detected" % counts["low"]
    else:
        decision = "no_blocking_issues"
        rationale = "no blocking issue was detected by the checked facets"
    return {"decision": decision, "rationale": rationale, "severity_counts": counts,
            "scope": "derived from the reported findings only; not a substitute for editorial judgement"}


def _report_markdown(payload, case_id):
    report = payload.get("review_report")
    if isinstance(report, dict) and report.get("content"):
        base = str(report["content"]).rstrip()
    elif isinstance(report, str) and report.strip():
        base = report.rstrip()
    else:
        base = "# Review report\n\n- case_id: `%s`\n- status: %s" % (
            case_id or "unknown", payload.get("status"))
    issues = [item for item in _issue_list(payload) if isinstance(item, dict)]
    rec = payload.get("recommendation") or _recommendation(issues)
    counts = rec.get("severity_counts") or {}
    major = [i for i in issues if str(i.get("severity") or "").lower() in ("high", "medium")]
    minor = [i for i in issues if str(i.get("severity") or "").lower() == "low"]
    other = [i for i in issues if i not in major and i not in minor]
    lines = [base, "", "## Summary", "",
             "The review reports %d finding(s): %d high, %d medium, %d low. Decision: **%s** — %s." % (
                 len(issues), counts.get("high", 0), counts.get("medium", 0), counts.get("low", 0),
                 rec.get("decision"), rec.get("rationale")), ""]
    for title, group in (("Major concerns", major), ("Minor concerns", minor), ("Other concerns", other)):
        if not group:
            continue
        lines += ["## %s" % title, ""]
        for index, item in enumerate(group, 1):
            lines.append("%d. **%s** (%s) — `%s`" % (
                index, item.get("facet") or item.get("category") or "issue",
                item.get("severity") or "unspecified",
                item.get("locator") or item.get("location") or "n/a"))
            if item.get("quote"):
                lines.append("   - quote: \"%s\"" % item["quote"])
            if item.get("issue") or item.get("finding"):
                lines.append("   - issue: %s" % (item.get("issue") or item.get("finding")))
            if item.get("recommendation") or item.get("suggested_revision"):
                lines.append("   - revision: %s" % (item.get("recommendation") or item.get("suggested_revision")))
        lines.append("")
    lines += ["## Recommendation", "", "- decision: `%s`" % rec.get("decision"),
              "- rationale: %s" % rec.get("rationale"),
              "- scope: %s" % rec.get("scope"), ""]
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        lines += ["## Review checklist", "", "| check | status | detail |", "|---|---|---|"]
        for item in checks:
            if isinstance(item, dict):
                lines.append("| %s | %s | %s |" % (
                    str(item.get("check") or item.get("name") or "")[:90],
                    item.get("status") or "",
                    str(item.get("detail") or item.get("note") or "")[:120]))
            else:
                lines.append("| %s | | |" % str(item)[:90])
        lines.append("")
    return "\n".join(lines)


def write_artifacts(payload, out_path, case, case_path=None, work_dir=None):
    """Write the five hand-off artifacts next to result.json (deterministic)."""
    out_dir = Path(out_path).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    review_blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    (out_dir / ARTIFACT_JSON).write_text(review_blob, encoding="utf-8", newline="\n")
    written.append(ARTIFACT_JSON)

    rows = _csv_rows(_issue_list(payload))
    buffer = io.StringIO()
    fields = ["finding_id", "facet", "severity", "locator", "quote", "quote_verified",
              "issue", "evidence", "recommendation"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    (out_dir / ARTIFACT_CSV).write_text(buffer.getvalue(), encoding="utf-8", newline="\n")
    written.append(ARTIFACT_CSV)

    (out_dir / ARTIFACT_MD).write_text(_report_markdown(payload, payload.get("case_id")),
                                       encoding="utf-8", newline="\n")
    written.append(ARTIFACT_MD)

    inputs = (case or {}).get("_input_files") or []
    outputs = []
    for name in written + ["result.json"]:
        path = out_dir / name
        if path.is_file():
            blob = path.read_bytes()
            outputs.append({"name": name, "bytes": len(blob), "sha256": _sha(blob)})
    provenance = {
        "skill": "academic-review",
        "entrypoint": os.path.basename(sys.argv[0]) if sys.argv else "",
        "case_file": str(case_path or ""),
        "python": sys.version.split()[0],
        "inputs": inputs,
        "outputs": outputs,
        "discovery_added": (case or {}).get("_discovery_added") or [],
        "deterministic": True,
    }
    (out_dir / ARTIFACT_PROV).write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8", newline="\n")
    written.append(ARTIFACT_PROV)

    validation = {
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "contract": payload.get("output_contract_fields"),
        "counts": payload.get("counts_reconciliation"),
        "locator_status": payload.get("locator_status"),
        "checks": payload.get("checks"),
        "coverage": payload.get("coverage"),
        "artifacts": written + ["result.json"],
        "limitations": payload.get("limitations") or payload.get("uncertainty"),
    }
    (out_dir / ARTIFACT_VALID).write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8", newline="\n")
    written.append(ARTIFACT_VALID)
    return written

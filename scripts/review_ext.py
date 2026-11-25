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


SECTION_ALIASES = {
    "abstract": ("abstract", "摘要"),
    "introduction": ("introduction", "background", "引言", "前言"),
    "methods": ("methods", "method", "materials and methods", "methodology", "方法", "材料与方法"),
    "results": ("results", "findings", "结果"),
    "discussion": ("discussion", "讨论"),
    "conclusion": ("conclusion", "conclusions", "结论"),
    "body": ("body", "full_text", "fulltext", "正文", "全文"),
}
TYPE_BY_FACET = {
    "numeric_consistency": "factual_error",
    "factual_consistency": "factual_error",
    "citation_support": "citation_error",
    "citation_integrity": "citation_error",
    "source_reliability": "citation_error",
    "methods_reporting": "method_issue",
    "design": "method_issue",
    "sampling": "method_issue",
    "reproducibility": "method_issue",
    "statistical_reporting": "statistical_issue",
    "statistics": "statistical_issue",
    "statistics_reporting": "statistical_issue",
    "argument_chain": "reasoning_gap",
    "inference": "reasoning_gap",
    "academic_norms": "norms_issue",
    "academic_norm": "norms_issue",
}


def _flatten_strings(value, depth=0):
    """Collect every string in a JSON-ish structure (dict values, incl. nested)."""
    out = []
    if depth > 8:
        return out
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, (int, float)):
        out.append(str(value))
    elif isinstance(value, list):
        for item in value:
            out.extend(_flatten_strings(item, depth + 1))
    elif isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            out.extend(_flatten_strings(item, depth + 1))
    return out


def _material_text(case):
    """All real input text, flattened — quotes are matched against this, never against str(dict)."""
    if not isinstance(case, dict):
        return ""
    return _norm(" ".join(_flatten_strings(case)))


def _first_sentence(text, limit=300):
    text = _norm(text)
    if not text:
        return ""
    for part in re.split(r"(?<=[.!?。！？])\s*", text):
        part = _norm(part)
        if len(part) >= 20:
            return part[:limit]
    return text[:limit]


def _section_texts(case):
    """Map lower-cased section names (and canonical aliases) to their raw text."""
    if not isinstance(case, dict):
        return {}
    raw = {}
    source = case.get("manuscript")
    if source in (None, "", [], {}):
        for key in ("paper", "text", "document", "article", "full_text"):
            if case.get(key) not in (None, "", [], {}):
                source = case.get(key)
                break
    if isinstance(source, dict):
        inner = source.get("sections") if isinstance(source.get("sections"), dict) else source
        for key, value in inner.items():
            text = _norm(" ".join(_flatten_strings(value)))
            if text:
                raw[str(key).strip().lower()] = text
    elif isinstance(source, str):
        current, buffer = "body", []
        for line in source.splitlines():
            candidate = _norm(line.strip("# ")).lower().rstrip(":")
            matched = None
            for canonical, aliases in SECTION_ALIASES.items():
                if candidate == canonical or candidate in aliases:
                    matched = canonical
                    break
            if matched and len(candidate) <= 40:
                if buffer:
                    raw.setdefault(current, _norm(" ".join(buffer)))
                current, buffer = matched, []
            else:
                buffer.append(line)
        if buffer:
            raw.setdefault(current, _norm(" ".join(buffer)))
    out = {}
    for key, text in raw.items():
        out.setdefault(key, text)
        for canonical, aliases in SECTION_ALIASES.items():
            if key == canonical or key in aliases:
                out.setdefault(canonical, text)
    return out


def _sentences(text):
    text = _norm(text)
    if not text:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s*", text) if len(part.strip()) >= 20]


def _anchor_empty_quote(item, sections, used):
    """Give absence-type findings an anchor sentence so the locator stays checkable."""
    quote = _norm(item.get("quote") or "")
    if quote:
        return False
    facet = str(item.get("facet") or item.get("category") or "").lower()
    locator = item.get("locator") or item.get("location") or ""
    probe = locator if isinstance(locator, str) else json.dumps(locator, ensure_ascii=False)
    if facet.startswith("academic_norm") or "未检出" in probe or "not found" in probe.lower():
        # an absence has no sentence to quote; the locator records where the search ran
        item["quote_role"] = "absent"
        return False
    probe = probe.lower()
    name_hit = ""
    for name in sorted(sections, key=len, reverse=True):
        if len(name) < 4:
            continue
        if re.search(r"(^|[^a-z0-9_])%s([^a-z0-9_]|$)" % re.escape(name), probe):
            name_hit = name
            break
    candidates = _sentences(sections.get(name_hit, "")) if name_hit else []
    anchor = ""
    if candidates:
        target = " ".join(str(item.get(k) or "") for k in
                           ("finding", "issue", "recommendation", "suggested_revision", "rationale"))
        words = set(re.findall(r"[A-Za-z\u4e00-\u9fff]{3,}", target.lower()))
        scored = []
        for index, sentence in enumerate(candidates):
            shared = len(words & set(re.findall(r"[A-Za-z\u4e00-\u9fff]{3,}", sentence.lower())))
            scored.append((-shared, (name_hit, index), sentence))
        for _, key, sentence in sorted(scored):
            if key not in used:
                anchor, chosen = sentence, key
                break
        if not anchor:
            anchor, chosen = sorted(scored)[0][2], sorted(scored)[0][1]
        used.add(chosen)
    if not anchor:
        item["quote_role"] = "absent"
        return False
    item["quote"] = anchor[:300]
    item["quote_role"] = "anchor"
    item["anchor_section"] = name_hit
    item["anchor_note"] = ("absence-type finding: the quoted sentence is the nearest anchor in the "
                           "referenced section, not evidence of the defect itself")
    return True


def _context_sentence(material, quote):
    """Sentence-level context around a short quote so the locator can be followed."""
    quote = _norm(quote)
    if not quote or not material:
        return ""
    pos = material.find(quote)
    if pos < 0:
        return ""
    start = max(material.rfind(". ", 0, pos), material.rfind("。", 0, pos), material.rfind("! ", 0, pos),
                material.rfind("? ", 0, pos)) + 1
    ends = [idx for idx in (material.find(". ", pos), material.find("。", pos), material.find("! ", pos),
                            material.find("? ", pos)) if idx >= 0]
    end = (min(ends) + 1) if ends else min(len(material), pos + 240)
    sentence = _norm(material[start:end])
    return sentence if len(sentence) >= 20 else ""


def _dedupe(issues):
    """Collapse findings that are identical in facet, quote phrase and revision."""
    seen, kept = {}, []
    removed = 0
    for item in issues:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        key = (str(item.get("facet") or item.get("category") or "").lower(),
               _norm(item.get("quote") or "")[:160],
               str(item.get("severity") or "").lower(),
               _norm(item.get("suggested_revision") or item.get("recommendation") or "")[:120])
        if key in seen:
            first = seen[key]
            merged = list(dict.fromkeys((first.get("citation_ids") or []) + (item.get("citation_ids") or [])))
            if merged:
                first["citation_ids"] = merged
            first["merged_duplicates"] = int(first.get("merged_duplicates") or 0) + 1
            removed += 1
            continue
        seen[key] = item
        kept.append(item)
    return kept, removed


def _issue_type(item):
    facet = str(item.get("facet") or item.get("category") or item.get("kind") or "").lower()
    return TYPE_BY_FACET.get(facet, "suggestion")


def _sync_counts(payload, issues):
    """Keep every published count block equal to the surviving findings after dedupe."""
    total = len(issues)
    by_sev = {}
    for item in issues:
        if isinstance(item, dict):
            sev = str(item.get("severity") or "unspecified").lower()
            by_sev[sev] = by_sev.get(sev, 0) + 1

    def _fix(block):
        if not isinstance(block, dict):
            return
        if isinstance(block.get("findings"), int):
            block["findings"] = total
        if isinstance(block.get("by_severity"), dict):
            block["by_severity"] = dict(sorted(by_sev.items()))
        if isinstance(block.get("total"), int) and ("by_severity" in block or "facets_covered" in block
                                                    or "verdict" in block):
            block["total"] = total

    for key in ("counts", "summary_counts", "summary"):
        _fix(payload.get(key))
    _fix(payload)
    nested = payload.get("result")
    if isinstance(nested, dict):
        for key in ("summary", "counts"):
            _fix(nested.get(key))
    return by_sev


def enrich(payload, case, case_path=None, work_dir=None):
    """Alias findings, verify quotes, reconcile counts, map contract keys to artifacts."""
    if not isinstance(payload, dict):
        return payload
    issues = _issue_list(payload)
    if not isinstance(payload.get("findings"), list):
        payload["findings"] = issues
    kept, removed = _dedupe(issues)
    if isinstance(issues, list):
        issues[:] = kept
    if removed:
        _sync_counts(payload, issues)
    material = _material_text(case if isinstance(case, dict) else {})
    sections = _section_texts(case if isinstance(case, dict) else {})
    anchored = 0
    used_anchors = set()
    for item in issues:
        if isinstance(item, dict):
            item.setdefault("issue_type", _issue_type(item))
            if item.get("issue_type") in (None, ""):
                item["issue_type"] = _issue_type(item)
            if _anchor_empty_quote(item, sections, used_anchors):
                anchored += 1
                continue
            quote = _norm(item.get("quote") or "")
            if quote and material and len(quote) < 12:
                context = _context_sentence(material, quote)
                if context:
                    item["quote_context"] = context
                    if CITE_MARK.match(quote):
                        item["quote_role"] = "citation_marker"
                        item.setdefault("anchor_note", "citation marker quoted; the surrounding "
                                                       "sentence is given as context")
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
        "anchored_absence_findings": anchored,
        "n_locator_issues": unverified,
        "method": "exact substring match of each finding quote against the actual input text",
    }
    by_sev = {}
    by_type = {}
    for item in issues:
        if isinstance(item, dict):
            sev = str(item.get("severity") or "unspecified").lower()
            by_sev[sev] = by_sev.get(sev, 0) + 1
            kind = str(item.get("issue_type") or _issue_type(item))
            by_type[kind] = by_type.get(kind, 0) + 1
    payload["counts_reconciliation"] = {
        "findings_total": len(issues),
        "by_severity": by_sev,
        "by_type": by_type,
        "severity_sum": sum(by_sev.values()),
        "dedup_removed": removed,
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
            "issue_type": item.get("issue_type") or _issue_type(item),
            "severity": item.get("severity") or "",
            "locator": item.get("locator") or item.get("location") or "",
            "quote": item.get("quote") or "",
            "quote_role": item.get("quote_role") or "",
            "quote_context": item.get("quote_context") or "",
            "quote_verified": "" if item.get("quote_verified") is None else str(item.get("quote_verified")).lower(),
            "issue": item.get("issue") or item.get("finding") or item.get("verdict") or "",
            "evidence": item.get("evidence") or item.get("rationale") or item.get("anchor_note") or "",
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
            lines.append("%d. **%s / %s** (%s) — `%s`" % (
                index, item.get("facet") or item.get("category") or "issue",
                item.get("issue_type") or _issue_type(item),
                item.get("severity") or "unspecified",
                item.get("locator") or item.get("location") or "n/a"))
            if item.get("quote"):
                suffix = " (anchor)" if item.get("quote_role") == "anchor" else ""
                lines.append("   - quote%s: \"%s\"" % (suffix, item["quote"]))
            if item.get("quote_context"):
                lines.append("   - context: \"%s\"" % item["quote_context"])
            if item.get("issue") or item.get("finding"):
                lines.append("   - issue: %s" % (item.get("issue") or item.get("finding")))
            if item.get("recommendation") or item.get("suggested_revision"):
                lines.append("   - revision: %s" % (item.get("recommendation") or item.get("suggested_revision")))
        lines.append("")
    loc = payload.get("locator_status") or {}
    if isinstance(loc, dict) and loc.get("quotes_total"):
        lines += ["## Locator verification", "",
                  "- quotes checked: %s (verified %s, not applicable %s, unverified %s)" % (
                      loc.get("quotes_total"), loc.get("verified"), loc.get("not_applicable"),
                      loc.get("unverified")),
                  "- anchored absence findings: %s" % loc.get("anchored_absence_findings", 0),
                  "- method: %s" % loc.get("method"), ""]
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
    fields = ["finding_id", "facet", "issue_type", "severity", "locator", "quote", "quote_role",
              "quote_context", "quote_verified", "issue", "evidence", "recommendation"]
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

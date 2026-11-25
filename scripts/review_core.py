"""Shared evidence-verification core (stdlib only).

Additive post-processor for academic-review Skill packages. It does NOT change
existing verdicts; it makes every quoted evidence fragment re-checkable:

* builds a paragraph index from the ACTUAL case material (list items, dict
  sections, blank-line blocks) and re-locates each quote to `paragraph_id`;
* marks each quote verified / unverified against the real text;
* writes checklist.csv, validation.json and provenance.json next to result.json;
* records the artifacts and an explicit human_confirm list inside the result.

No network, no randomness, no host absolute paths in the emitted JSON.
"""

import csv
import json
import os
import re

_PUNCT = re.compile(r"[\s\u3000]+")


def _norm(text):
    return _PUNCT.sub(" ", str(text or "")).strip().lower()


def _collect(node, out):
    if isinstance(node, str):
        if len(node.strip()) >= 2:
            out.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect(item, out)
    elif isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in ("output_contract", "case_id", "url", "doi"):
                continue
            _collect(value, out)


def build_paragraphs(case, input_dir=None):
    """Return [(paragraph_id, text)] from the actual case material."""
    raw = []
    _collect(case, raw)
    if input_dir and os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            path = os.path.join(input_dir, name)
            if not os.path.isfile(path) or os.path.getsize(path) > 400000:
                continue
            if not name.lower().endswith((".md", ".txt", ".json", ".jsonl", ".csv", ".xml")):
                continue
            if name in ("result.json", "validation.json", "provenance.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    raw.append(handle.read())
            except OSError:
                continue
    paragraphs = []
    for chunk in raw:
        blocks = re.split(r"\n\s*\n", chunk)
        for block in blocks:
            block = block.strip()
            if len(block) < 20:
                continue
            if "\n" in block and len(block) > 400:
                for line in block.split("\n"):
                    line = line.strip()
                    if len(line) >= 20:
                        paragraphs.append(line)
            else:
                paragraphs.append(block)
    seen = set()
    result = []
    for text in paragraphs:
        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(("p%d" % (len(result) + 1), text))
    return result


def locate(quote, paragraphs):
    needle = _norm(quote)
    if len(needle) < 8:
        return None
    for pid, text in paragraphs:
        if needle in _norm(text):
            return pid
    return None


def _iter_quote_rows(payload):
    result = payload.get("result")
    containers = [result, payload] if isinstance(result, dict) else [payload]
    for container in containers:
        for key in ("findings", "issues", "problems", "claims", "evidence"):
            rows = container.get(key)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        yield row
    evidence = payload.get("evidence")
    if isinstance(evidence, list):
        for row in evidence:
            if isinstance(row, dict):
                yield row


def _support_rollup(result):
    rank = {"contradicts": 0, "unrelated": 1, "insufficient": 2, "partial": 3, "supports": 4}
    name = {v: k for k, v in rank.items()}
    rows = []
    claims = result.get("claims")
    if not isinstance(claims, list):
        return rows
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        subs = claim.get("subclaims")
        if not isinstance(subs, list) or not subs:
            continue
        vals = [str(s.get("support", "")).lower() for s in subs if isinstance(s, dict)]
        vals = [v for v in vals if v in rank]
        if not vals:
            continue
        worst = min(vals, key=lambda v: rank[v])
        declared = str(claim.get("support", "")).lower()
        rows.append({
            "claim_id": claim.get("id", ""),
            "subclaims": len(vals),
            "computed_support": name[worst],
            "declared_support": declared,
            "override": bool(declared and declared != name[worst]),
        })
    return rows


def _metadata_checks(result):
    rows = []
    refs = result.get("references")
    if not isinstance(refs, list):
        return rows
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        cited = ref.get("cited") if isinstance(ref.get("cited"), dict) else {}
        actual = ref.get("actual") if isinstance(ref.get("actual"), dict) else {}
        flags = []
        if cited.get("doi") and actual.get("doi"):
            if _norm(cited["doi"]).replace("https://doi.org/", "").rstrip(".") != \
                    _norm(actual["doi"]).replace("https://doi.org/", "").rstrip("."):
                flags.append("doi_mismatch")
        if cited.get("year") and actual.get("year") and str(cited["year"]) != str(actual["year"]):
            flags.append("year_mismatch")
        if flags:
            rows.append({"ref_id": ref.get("ref_id", ""), "flags": flags})
    return rows


def enrich(payload, case, out_path, input_dir=None):
    """Add verification data to payload and write the auxiliary artifacts."""
    if not isinstance(payload, dict):
        return payload
    result = payload.get("result")
    container = result if isinstance(result, dict) else payload
    extra = container
    if container is payload and isinstance(payload.get("contract_extras"), dict):
        extra = payload["contract_extras"]
    paragraphs = build_paragraphs(case, input_dir)
    total = verified = 0
    unverified = []
    for row in _iter_quote_rows(payload):
        quote = row.get("quote") or row.get("excerpt") or row.get("text_quote")
        if not isinstance(quote, str) or len(quote.strip()) < 8:
            continue
        total += 1
        pid = locate(quote, paragraphs)
        if pid:
            verified += 1
            row["paragraph_id"] = pid
            row["quote_verified"] = True
        else:
            row["quote_verified"] = False
            unverified.append({"quote": quote[:160], "locator": row.get("locator", "")})
    rollup = _support_rollup(container)
    meta = _metadata_checks(container)
    rate = round(verified / total, 4) if total else 0.0
    verification = {
        "paragraph_count": len(paragraphs),
        "quotes_checked": total,
        "quotes_verified": verified,
        "verification_rate": rate,
        "unverified_quotes": unverified[:20],
        "support_rollup": rollup,
        "metadata_mismatches": meta,
        "method": "exact normalized-substring match of every quoted fragment against the actual case material",
    }
    extra["verification"] = verification
    artifacts = ["result.json", "review-report.md", "checklist.csv", "validation.json", "provenance.json"]
    extra["artifacts"] = artifacts
    if unverified:
        confirm = extra.get("human_confirm")
        if not isinstance(confirm, list):
            confirm = []
        for item in unverified[:20]:
            confirm.append({"item": "quote_not_relocatable", "quote": item["quote"],
                            "handling": "confirm the quoted text and its paragraph before relying on it"})
        extra["human_confirm"] = confirm

    out_dir = os.path.dirname(os.path.abspath(out_path)) if out_path else ""
    if not out_dir:
        return payload
    case_id = payload.get("case_id", "")
    try:
        csv_path = os.path.join(out_dir, "checklist.csv")
        if not os.path.isfile(csv_path):
            _write_csv(csv_path, container)
        _merge_json(os.path.join(out_dir, "validation.json"), {
            "case_id": case_id,
            "generated_from": "actual case material",
            "quotes_checked": total,
            "quotes_verified": verified,
            "verification_rate": rate,
            "unverified_quotes": unverified[:20],
            "checks": payload.get("checks", []),
        })
        _merge_json(os.path.join(out_dir, "provenance.json"), {
            "case_id": case_id,
            "pipeline": ["load case", "build paragraph index", "audit facets",
                         "verify quotes against material", "write artifacts"],
            "paragraph_count": len(paragraphs),
            "artifacts": artifacts,
            "change_log": container.get("change_log", []),
        })
    except OSError:
        pass
    return payload


def _merge_json(path, patch):
    base = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                base = loaded
        except (OSError, ValueError):
            base = {}
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key].update(value)
        else:
            base[key] = value
    _write_json(path, base)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _write_csv(path, result):
    rows = []
    for key in ("findings", "issues"):
        items = result.get(key)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                rows.append([key, item.get("facet", item.get("item", "")), item.get("severity", ""),
                             item.get("locator", ""), item.get("paragraph_id", ""),
                             item.get("status", ""), str(item.get("recommendation", ""))[:200]])
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["section", "facet", "severity", "locator", "paragraph_id", "status", "recommendation"])
        writer.writerows(rows)

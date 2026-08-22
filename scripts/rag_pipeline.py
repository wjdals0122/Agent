"""
Orchestrator: runs the 4 doc-group parsers over the whole manifest, attaches
a company-name/alias/metadata header (via CompanyAliasRegistry, unified
across all 4 doc groups unless --no-unify-aliases), and saves the result to
corpus/rag/*.md using {회사명}_{문서군}_{문서ID}[_{첨부번호}].md.

Usage:
  python rag_pipeline.py [--limit N] [--doc-group GROUP] [--keep-empty]
                         [--no-unify-aliases] [--json STATS_PATH]
"""
import argparse
import json
import os
import re
import traceback

import lv_render
import major_parser
import holding_parser
import periodic_parser
import exchange_parser
from alias_registry import CompanyAliasRegistry
from config import CORPUS_DIR, RAG_DIR, MANIFEST_PATH

PARSERS = {
    "exchange": exchange_parser,
    "major": major_parser,
    "holding": holding_parser,
    "periodic": periodic_parser,
}

INVALID_FS_CHARS = '\\/:*?"<>|'


def sanitize_filename(s):
    return "".join(c for c in s if c not in INVALID_FS_CHARS).strip()


def load_manifest():
    docs = []
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))
    return docs


_LEGAL_FORM_RE = re.compile(
    r"\(\s*주\s*\)|주식회사|㈜|\(\s*유\s*\)|유한회사|\(\s*재\s*\)|재단법인"
    r"|\(\s*사\s*\)|사단법인|Co\.,?\s*Ltd\.?|Inc\.?|Corp\.?",
    re.IGNORECASE,
)


def _strip_legal_form(name):
    if not name:
        return ""
    return re.sub(r"\s{2,}", " ", _LEGAL_FORM_RE.sub("", name)).strip(" .,")


def build_alias_candidates(manifest_doc, discovered_aliases):
    """Search-term candidates for this company: the manifest's own corp_name/
    listed_name, a bare (legal-form-stripped) variant, and mechanically
    generated "(주)X"/"X주식회사" forms alongside whatever real historical
    name variants CompanyAliasRegistry actually observed in the raw corpus
    (discovered_aliases) - order-preserving de-dup."""
    corp = (manifest_doc.get("corp_name") or "").strip()
    listed = (manifest_doc.get("listed_name") or "").strip()
    candidates = []
    if corp:
        bare = _strip_legal_form(corp)
        if bare:
            candidates += [f"(주){bare}", f"{bare}주식회사"]
        candidates.append(corp)
        if bare and bare != corp:
            candidates.append(bare)
    if listed:
        candidates.append(listed)
    candidates += list(discovered_aliases)

    seen, out = set(), []
    for a in candidates:
        a = re.sub(r"\s{2,}", " ", str(a)).strip()
        if a and a != corp and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def build_header(manifest_doc, parsed, aliases):
    """RAG-embedding-friendly header: a natural-language first line a query
    can match directly, a compact metadata line, and an explicit alias line
    (so a search for an old/abbreviated company name still hits this doc),
    plus the traceability fields (접수번호 등) a later step may need to map
    a chunk back to its exact source document."""
    corp = manifest_doc.get("corp_name") or "미상"
    report = manifest_doc.get("report_nm") or "(보고서명 없음)"
    stock = manifest_doc.get("stock_code") or "-"
    industry = manifest_doc.get("industry") or "-"
    sector = manifest_doc.get("sector") or ""
    industry_display = f"{industry} > {sector}" if sector else industry

    lines = [f"# [{corp}] {report}"]
    lines.append(f"> 표준사명: {corp} | 종목코드: {stock} | 업종: {industry_display}")
    if aliases:
        lines.append(f"> 동의어/검색어: {', '.join(aliases)}")

    trace = [f"문서군: {manifest_doc.get('doc_group', '')}"]
    if manifest_doc.get("doc_subtype"):
        trace.append(f"문서유형: {manifest_doc['doc_subtype']}")
    trace.append(f"접수번호: {manifest_doc.get('rcept_no', '')}")
    trace.append(f"접수일자: {manifest_doc.get('rcept_dt', '')}")
    is_corr_manifest = manifest_doc.get("is_correction")
    is_corr_parsed = parsed.is_correction_tag
    corr_display = f"정정공시: manifest={is_corr_manifest}"
    if is_corr_parsed is not None:
        corr_display += f", 본문판별={is_corr_parsed}"
    trace.append(corr_display)
    lines.append(f"> {' | '.join(trace)}")

    return "\n".join(lines) + "\n\n"


def output_filename(manifest_doc, attachment_suffix):
    company = sanitize_filename(manifest_doc.get("corp_name", "") or "UNKNOWN")
    doc_group = manifest_doc["doc_group"]
    rcept_no = manifest_doc["rcept_no"]
    base = f"{company}_{doc_group}_{rcept_no}"
    if attachment_suffix:
        base += f"_{attachment_suffix}"
    return base + ".md"


def run(limit=None, doc_group_filter=None, keep_empty=False, unify_aliases=True, out_dir=RAG_DIR):
    os.makedirs(out_dir, exist_ok=True)
    lv_render.set_keep_empty(keep_empty)

    docs = load_manifest()
    if doc_group_filter:
        docs = [d for d in docs if d["doc_group"] == doc_group_filter]
    if limit:
        docs = docs[:limit]

    registry = None
    if unify_aliases:
        registry = CompanyAliasRegistry().build(raw_root=CORPUS_DIR)

    stats = {
        "docs_attempted": 0,
        "docs_ok": 0,
        "docs_failed": 0,
        "files_written": 0,
        "warnings_total": 0,
        "by_doc_group": {},
        "failures": [],
        "warnings_detail": [],
    }

    for d in docs:
        group = d["doc_group"]
        gstat = stats["by_doc_group"].setdefault(
            group, {"attempted": 0, "ok": 0, "failed": 0, "files_written": 0}
        )
        stats["docs_attempted"] += 1
        gstat["attempted"] += 1

        parser_mod = PARSERS[group]
        try:
            results = parser_mod.parse(d)
        except Exception as e:
            stats["docs_failed"] += 1
            gstat["failed"] += 1
            stats["failures"].append({
                "doc_id": d["doc_id"], "doc_group": group,
                "error": repr(e), "traceback": traceback.format_exc(limit=3),
            })
            continue

        if not results:
            stats["docs_failed"] += 1
            gstat["failed"] += 1
            stats["failures"].append({
                "doc_id": d["doc_id"], "doc_group": group, "error": "no_files_resolved",
            })
            continue

        doc_ok = True
        for r in results:
            parsed = r.parsed
            if parsed.warnings:
                stats["warnings_total"] += len(parsed.warnings)
                stats["warnings_detail"].append({
                    "doc_id": d["doc_id"], "file": r.file_path, "warnings": parsed.warnings,
                })

            discovered = registry.get_aliases(d.get("corp_code"), exclude=d.get("corp_name")) if registry else []
            aliases = build_alias_candidates(d, discovered)

            header = build_header(d, parsed, aliases)
            full_md = header + parsed.markdown

            fname = output_filename(d, r.attachment_suffix)
            with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
                f.write(full_md)
            stats["files_written"] += 1
            gstat["files_written"] += 1

        if doc_ok:
            stats["docs_ok"] += 1
            gstat["ok"] += 1

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--doc-group", default=None, choices=list(PARSERS.keys()))
    ap.add_argument("--keep-empty", action="store_true")
    ap.add_argument("--no-unify-aliases", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--out-dir", default=RAG_DIR)
    args = ap.parse_args()

    stats = run(
        limit=args.limit,
        doc_group_filter=args.doc_group,
        keep_empty=args.keep_empty,
        unify_aliases=not args.no_unify_aliases,
        out_dir=args.out_dir,
    )
    print(json.dumps({k: v for k, v in stats.items() if k not in ("failures", "warnings_detail")},
                      ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

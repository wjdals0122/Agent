"""
Structural-loss check (the "구조 직접 대조" tier from the original exceptions
spec, previously run only for holding/periodic - this extends it to all 4
groups): for each sampled document, count (a) tables whose cells have real
text but NONE of that text survives anywhere in the rendered markdown
("표 전체 소실" - whole-table loss) and (b) how many of the source's own
section/heading markers (<TITLE> for DSD-XML, xforms_title divs for
exchange's real HTML) made it into the output as a markdown heading.

This is a coarser, faster check than validate_token_loss.py's token-set
diff - it answers "did any whole table vanish?" and "are headings roughly
intact?", which a token-set comparison can miss (a table can lose all its
own tokens to duplicates found elsewhere in the same document).

Usage: python validate_structural.py --per-group N [--out results.json]
"""
import argparse
import json
import os
import random
import re

from common_parse import parse_markup, collapse_ws
from config import CORPUS_DIR, RAG_DIR, MANIFEST_PATH


def load_manifest():
    docs = []
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))
    return docs


def output_text_for_doc(manifest_doc):
    company = manifest_doc.get("corp_name", "") or "UNKNOWN"
    company = "".join(c for c in company if c not in '\\/:*?"<>|').strip()
    prefix = f"{company}_{manifest_doc['doc_group']}_{manifest_doc['rcept_no']}"
    texts = []
    for fn in os.listdir(RAG_DIR):
        if fn.startswith(prefix) and fn.endswith(".md"):
            with open(os.path.join(RAG_DIR, fn), encoding="utf-8") as f:
                texts.append(f.read())
    return "\n".join(texts) if texts else None


def check_doc(manifest_doc):
    dir_path = os.path.join(CORPUS_DIR, manifest_doc["file_path"])
    if not os.path.isdir(dir_path):
        return None
    out_text = output_text_for_doc(manifest_doc)
    if out_text is None:
        return None
    # headings nest as "##".."######" (dsd_walker._heading_level caps at 6),
    # not always exactly "## " - counting only "## " badly undercounts any
    # document with SECTION-2/3/4 nesting.
    out_heading_count = sum(1 for ln in out_text.splitlines() if re.match(r"^#{1,6} ", ln))

    is_exchange = manifest_doc["doc_group"] == "exchange"
    lost_tables = 0
    total_tables_with_text = 0
    src_heading_count = 0

    for fn in sorted(os.listdir(dir_path)):
        if not fn.lower().endswith(".xml"):
            continue
        with open(os.path.join(dir_path, fn), "rb") as f:
            raw = f.read()
        root = parse_markup(raw)

        if is_exchange:
            for div in root.find_all("DIV"):
                if "xforms_title" in (div.get("CLASS") or ""):
                    if collapse_ws(div.text_content()):
                        src_heading_count += 1
            tables = root.find_all("TABLE")
        else:
            for title in root.find_all("TITLE"):
                if collapse_ws(title.text_content()):
                    src_heading_count += 1
            tables = root.find_all("TABLE")

        for t in tables:
            cell_texts = [
                collapse_ws(c.text_content())
                for c in t.iter_elements()
                if c.tag in ("TD", "TE", "TU", "TH")
            ]
            meaningful = [c for c in cell_texts if len(c) >= 3]
            if not meaningful:
                continue
            total_tables_with_text += 1
            sample = meaningful[:5] + meaningful[-5:]
            if not any(s in out_text for s in sample):
                lost_tables += 1

    return {
        "doc_id": manifest_doc["doc_id"],
        "tables_with_text": total_tables_with_text,
        "tables_fully_lost": lost_tables,
        "src_heading_count": src_heading_count,
        "out_heading_count": out_heading_count,
        "heading_ratio": round(out_heading_count / src_heading_count, 3) if src_heading_count else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-group", type=int, default=40)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args()

    docs = [d for d in load_manifest() if d.get("file_format") == "xml"]
    by_group = {}
    for d in docs:
        by_group.setdefault(d["doc_group"], []).append(d)

    rng = random.Random(args.seed)
    results = {}
    for group, group_docs in by_group.items():
        sample = rng.sample(group_docs, min(args.per_group, len(group_docs)))
        per_doc = [r for r in (check_doc(d) for d in sample) if r is not None]
        total_tables = sum(p["tables_with_text"] for p in per_doc)
        total_lost = sum(p["tables_fully_lost"] for p in per_doc)
        ratios = [p["heading_ratio"] for p in per_doc if p["heading_ratio"] is not None]
        avg_heading_ratio = sum(ratios) / len(ratios) if ratios else None
        worst_docs = sorted(per_doc, key=lambda p: -p["tables_fully_lost"])[:5]
        results[group] = {
            "sample_size": len(per_doc),
            "total_tables_with_text": total_tables,
            "total_tables_fully_lost": total_lost,
            "avg_heading_ratio": round(avg_heading_ratio, 3) if avg_heading_ratio is not None else None,
            "worst_docs": worst_docs,
        }
        print(f"{group}: sample={len(per_doc)} tables_checked={total_tables} "
              f"tables_fully_lost={total_lost} avg_heading_ratio={results[group]['avg_heading_ratio']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

"""
Token-level content-loss check, applied uniformly across all 4 doc groups
(the original exceptions spec only ran this for holding/periodic; this run
extends the same method to exchange/major too, closing that gap).

Method: strip all markup from the raw source file(s) for a document, tokenize
into 3+-char Korean/alphanumeric runs, and compare against the same
tokenization of the rendered markdown output. Tokens present in source but
absent from output are reported as candidate loss (not proof of a bug - some
are intentionally dropped metadata, e.g. <EXTRACTION>/<SUMMARY> field codes,
or CSS from <STYLE> blocks - those categories are called out separately).

Usage: python validate_token_loss.py --per-group N [--out results.json]
"""
import argparse
import json
import os
import random
import re

from config import CORPUS_DIR, RAG_DIR, MANIFEST_PATH

TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{3,}")
TAG_RE = re.compile(r"<[^>]*>")


def tokenize(text):
    return set(TOKEN_RE.findall(text))


def source_tokens(manifest_doc, exclude_tags=("STYLE", "SCRIPT", "EXTRACTION", "SUMMARY")):
    """Tokens from the raw source, with named metadata/CSS blocks excluded up
    front (those are deliberately not rendered - see dsd_walker.SKIP_TAGS /
    exchange_parser's STYLE handling) so they don't inflate apparent loss."""
    dir_path = os.path.join(CORPUS_DIR, manifest_doc["file_path"])
    if not os.path.isdir(dir_path):
        return None
    all_tokens = set()
    found_any = False
    for fn in sorted(os.listdir(dir_path)):
        if not fn.lower().endswith(".xml"):
            continue
        found_any = True
        with open(os.path.join(dir_path, fn), encoding="utf-8", errors="replace") as f:
            text = f.read()
        for tag in exclude_tags:
            text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", text, flags=re.S | re.I)
        text = TAG_RE.sub(" ", text)
        all_tokens |= tokenize(text)
    return all_tokens if found_any else None


def output_tokens_for_doc(manifest_doc):
    company = manifest_doc.get("corp_name", "") or "UNKNOWN"
    company = "".join(c for c in company if c not in '\\/:*?"<>|').strip()
    prefix = f"{company}_{manifest_doc['doc_group']}_{manifest_doc['rcept_no']}"
    tokens = set()
    found_any = False
    for fn in os.listdir(RAG_DIR):
        if fn.startswith(prefix) and fn.endswith(".md"):
            found_any = True
            with open(os.path.join(RAG_DIR, fn), encoding="utf-8") as f:
                tokens |= tokenize(f.read())
    return tokens if found_any else None


def load_manifest():
    docs = []
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-group", type=int, default=40)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args()

    docs = load_manifest()
    docs = [d for d in docs if d.get("file_format") == "xml"]  # pdf+html docs have no XML to compare against
    by_group = {}
    for d in docs:
        by_group.setdefault(d["doc_group"], []).append(d)

    rng = random.Random(args.seed)
    results = {}
    for group, group_docs in by_group.items():
        sample = rng.sample(group_docs, min(args.per_group, len(group_docs)))
        per_doc = []
        for d in sample:
            src = source_tokens(d)
            out = output_tokens_for_doc(d)
            if src is None or out is None:
                per_doc.append({"doc_id": d["doc_id"], "skipped": True})
                continue
            missing = src - out
            loss_pct = (len(missing) / len(src) * 100) if src else 0.0
            per_doc.append({
                "doc_id": d["doc_id"],
                "src_token_count": len(src),
                "out_token_count": len(out),
                "missing_count": len(missing),
                "loss_pct": round(loss_pct, 3),
                "missing_sample": sorted(missing)[:25],
            })
        valid = [p for p in per_doc if not p.get("skipped")]
        avg_loss = sum(p["loss_pct"] for p in valid) / len(valid) if valid else None
        worst = sorted(valid, key=lambda p: -p["loss_pct"])[:5]
        results[group] = {
            "sample_size": len(sample),
            "valid": len(valid),
            "avg_loss_pct": round(avg_loss, 3) if avg_loss is not None else None,
            "worst_5": worst,
            "per_doc": per_doc,
        }
        print(f"{group}: sample={len(sample)} avg_loss_pct={results[group]['avg_loss_pct']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

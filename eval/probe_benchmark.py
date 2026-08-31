"""벤치마크 질문(사람이 만든 정답 포함)으로 **검색 근거**가 잡히는지 본다.

답변 생성기(단계 5-A)는 아직 없다. 여기서 보는 것은
"정답 숫자가 들어 있는 청크를 검색기가 상위 k 안에 물어오는가" 뿐이다.

  python eval/probe_benchmark.py --k 20
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from src.index import paths
from src.search.aliases import resolve_company
from src.search.bm25 import BM25

QDIR = ROOT / "Question" / "질문_(수정해서 다시 할 것)"
NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def load_cases(scenario: str) -> list[dict]:
    out = []
    for p in sorted(glob.glob(str(QDIR / "benchmark_*AR*.json"))):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        sc = d["scenarios"].get(scenario)
        if not sc:
            continue
        nums = NUM.findall(sc["ground_truth"])
        out.append({
            "file": Path(p).name,
            "company": d["company"],
            "fy": re.search(r"_(\d{4})AR", p).group(1),
            "query": sc["query"],
            "gt": sc["ground_truth"],
            "answer_nums": nums,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--scenario", default="search_closed")
    args = ap.parse_args()

    bm = BM25()
    idm = pd.read_parquet(paths.ID_MAP)
    stock = idm["corp_code"].astype(str).str.zfill(6).to_numpy()   # 실제로는 종목코드
    mask = np.load(ROOT / "data" / "index" / "latest_mask.npz")
    is_latest = mask["is_latest"]

    cases = load_cases(args.scenario)
    print(f"{args.scenario}: {len(cases)}건 (숫자 정답 있는 것 "
          f"{sum(1 for c in cases if c['answer_nums'])}건) · top-{args.k}\n")

    rows_hit = exists = resolved = 0
    lat = []
    detail = []
    for c in cases:
        r = resolve_company(c["company"])
        if r["status"] != "ok":
            detail.append((c, r["status"], None, None, None))
            continue
        resolved += 1
        cand = np.flatnonzero((stock == r["stock_code"]) & is_latest)

        # 정답 숫자를 담은 청크가 이 회사 안에 실제로 있는가 (검색 성능의 상한)
        gold = set()
        for nstr in c["answer_nums"][:3]:
            j = bm.vocab.get(nstr)
            if j is None:
                continue
            s, e = bm.indptr[j], bm.indptr[j + 1]
            gold |= set(bm.indices[s:e].tolist())
        gold &= set(cand.tolist())

        t0 = time.time()
        top, sc = bm.top_k(c["query"], args.k, rows=cand)
        lat.append((time.time() - t0) * 1000)

        has_gold = bool(gold)
        hit = bool(gold & set(int(x) for x in top))
        exists += has_gold
        rows_hit += hit
        detail.append((c, "ok", len(cand), len(gold), hit))

    print(f"{'회사':<16}{'FY':<6}{'후보행':>9}{'정답청크':>9}{'top-'+str(args.k):>8}  질문 앞 44자")
    for c, st, ncand, ngold, hit in detail:
        if st != "ok":
            print(f"{c['company']:<16}{c['fy']:<6}{'-':>9}{'-':>9}{st:>8}  {c['query'][:44]}")
            continue
        print(f"{c['company']:<16}{c['fy']:<6}{ncand:>9,}{ngold:>9}"
              f"{('HIT' if hit else ('MISS' if ngold else 'n/a')):>8}  {c['query'][:44]}")

    n = len(cases)
    print(f"\n회사 해석 성공      {resolved}/{n}")
    print(f"정답 숫자가 코퍼스에 존재 {exists}/{n}   ← 검색기가 잡을 수 있는 상한")
    print(f"top-{args.k} 안에 정답 청크  {rows_hit}/{n}"
          f"  (존재하는 것 대비 {rows_hit/exists:.1%})" if exists else "")
    print(f"BM25 검색 지연 평균 {np.mean(lat):.1f}ms / 최대 {max(lat):.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

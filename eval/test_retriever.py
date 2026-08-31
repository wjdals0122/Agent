"""1-C 검증 — 하이브리드 검색기.

  python eval/test_retriever.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from src.search.aliases import all_companies
from src.search.hybrid import HybridSearch

FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}", flush=True)
    if not ok:
        FAIL.append(name)
    return ok


def main() -> int:
    hs = HybridSearch()
    print()

    # 1. 필터가 붙었는가 (품질보다 이게 먼저)
    r1 = hs.search("연결 기준 매출액", corp_name="삼성전자", base_year=2024, top_k=5)
    check("1. 삼성전자·2024 후보 > 0",
          r1["n_candidates"] > 0,
          f"n_candidates={r1['n_candidates']:,} status={r1['status']} "
          f"결과 {len(r1['results'])}건 {r1.get('elapsed_ms')}ms")

    # 2. 70개사 전수 — corp_code/stock_code 혼동을 잡는다
    zero = []
    for c in all_companies():
        r = hs.search("매출액", corp_name=c["corp_name"], top_k=1)
        if r["n_candidates"] == 0:
            zero.append(c["corp_name"])
    check("2. 70개사 전수 n_candidates > 0", not zero,
          f"0건 회사 {len(zero)}곳 {zero}")

    # 3. 타사 혼입 0건
    bad = [x["source"]["company"] for x in r1["results"] if x["source"]["company"] != "삼성전자"]
    check("3. 결과 전부 삼성전자", not bad, f"타사 {bad}")

    # 4. 다양성 + 중복 제거
    docs = {x["doc_id"] for x in r1["results"]}
    shas = [x["chunk_id"] for x in r1["results"]]
    check("4. doc_id 2종 이상 · chunk 중복 0",
          len(docs) >= 2 and len(shas) == len(set(shas)),
          f"doc {len(docs)}종 / 결과 {len(shas)}건")

    # 5. CJ제일제당 정정 계열 — 구본이 빠지는가 / 필요한 때만 남는가
    a = hs.search("매출액", corp_name="CJ제일제당", fiscal_year=2024, top_k=10)
    b = hs.search("감사의견 독립된 감사인", corp_name="CJ제일제당", fiscal_year=2024, top_k=10)
    ra = sorted({x["source"]["receipt_no"] for x in a["results"]})
    rb = sorted({x["source"]["receipt_no"] for x in b["results"]})
    check("5a. 매출액 질의에 원본 20250317000648 없음",
          "20250317000648" not in ra, f"receipt_no={ra} (드롭 {a['n_preserved_dropped']}건)")
    check("5b. 감사의견 질의에 원본 20250317000648 있음",
          "20250317000648" in rb, f"receipt_no={rb} (드롭 {b['n_preserved_dropped']}건)")

    # 6. 0건·모호 처리
    r = hs.search("매출", corp_name="삼성전자", base_year=2099)
    check("6a. base_year=2099 → not_found", r["status"] == "not_found", r["note"])
    r = hs.search("매출", corp_name="없는회사")
    check("6b. 없는회사 → not_found", r["status"] == "not_found", r["note"])
    r = hs.search("매출", corp_name="SK")
    check("6c. SK → ambiguous + candidates",
          r["status"] == "ambiguous" and len(r.get("candidates", [])) >= 2,
          ", ".join(c["corp_name"] for c in r.get("candidates", [])))

    # 7. 지연시간
    for label, kw in (("회사 지정", {"corp_name": "삼성전자", "base_year": 2024}),
                      ("광역(필터 없음)", {})):
        hs.search("연결 기준 매출액", top_k=5, **kw)          # 워밍업
        ts = [hs.search("연결 기준 매출액", top_k=5, **kw)["elapsed_ms"] for _ in range(5)]
        check(f"7. {label} 지연 ≤ 300ms", max(ts) <= 300,
              f"평균 {np.mean(ts):.0f}ms · 최대 {max(ts):.0f}ms")

    # 참고 출력
    print("\n=== 1번 질의 top-5 ===")
    for x in r1["results"]:
        s = x["source"]
        print(f"\n{x['rank']}. {x['chunk_id']}  rrf={x['rrf']:.5f} "
              f"bm25={x['bm25']:.2f} dense={x['dense']:.3f}")
        print(f"   {s['company']} · {s['report_nm']} · 접수 {s['rcept_dt']}({s['receipt_no']})")
        print(f"   {x['section_path'][:150]}")
        print(f"   {x['content'][:150].replace(chr(10), ' / ')}")

    print()
    if FAIL:
        print(f"실패 {len(FAIL)}건: {FAIL}")
        return 1
    print("전 항목 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

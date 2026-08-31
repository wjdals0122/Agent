"""1-A 검증 — 회사명 해석 + 토크나이저.

  python eval/test_aliases.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.index import paths
from src.search.aliases import all_companies, resolve_company
from src.search.tokenize_ko import DEFAULT_WORKERS, get_kiwi, tokenize, tokenize_batch

FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}", flush=True)
    if not ok:
        FAIL.append(name)
    return ok


def main() -> int:
    comps = all_companies()

    # 1. 70개사 corp_name / listed_name 왕복
    bad = []
    for c in comps:
        for field in ("corp_name", "listed_name"):
            r = resolve_company(c[field])
            if r["status"] != "ok" or r["stock_code"] != c["stock_code"]:
                bad.append(f"{c['corp_name']}.{field}={c[field]} → {r['status']}")
    check("1. 70개사 corp_name/listed_name 왕복 전부 ok",
          not bad, f"실패 {len(bad)}건" + (" | " + "; ".join(bad[:5]) if bad else ""))

    # 2. 반환 stock_code 집합 == id_map 의 corp_code 고유값 집합  ← 치명 A 방어선
    got = {c["stock_code"] for c in comps}
    idm = pd.read_parquet(paths.ID_MAP, columns=["corp_code"])
    have = set(idm["corp_code"].astype(str).str.zfill(6).unique())
    check("2. stock_code 70종 == id_map corp_code 70종",
          got == have,
          f"aliases {len(got)} / id_map {len(have)} / "
          f"aliases에만 {sorted(got - have)} / id_map에만 {sorted(have - got)}")

    # 3. 지정된 별칭 매핑
    want = {
        "현대차": "현대자동차", "KT": "케이티", "하이닉스": "SK하이닉스",
        "LIG넥스원": "LIG디펜스앤에어로스페이스", "엔씨소프트": "NC", "JYP": "JYP Ent",
    }
    bad3 = []
    for q, exp in want.items():
        r = resolve_company(q)
        if r["status"] != "ok" or r["corp_name"] != exp:
            bad3.append(f"{q} → {r.get('corp_name') or r['status']} (기대 {exp})")
    check("3. 지정 별칭 6건 매핑", not bad3, "; ".join(bad3) if bad3 else "6/6")

    # 4. not_found / ambiguous
    r = resolve_company("테슬라")
    check("4a. 테슬라 → not_found", r["status"] == "not_found", r["status"])
    r = resolve_company("SK")
    check("4b. SK → ambiguous (후보 2곳 이상)",
          r["status"] == "ambiguous" and len(r.get("candidates", [])) >= 2,
          f"{r['status']} / 후보 {len(r.get('candidates', []))}곳: "
          + ", ".join(c["corp_name"] for c in r.get("candidates", [])))

    # 5. 토큰화
    sent = "LIG넥스원의 2024년 유형자산 취득액은 3,908,761백만원이다"
    toks = tokenize(sent)
    check("5. 'lig넥스원' 한 덩어리 + '3908761' 존재",
          "lig넥스원" in toks and "3908761" in toks, str(toks))

    # 6. 처리량
    from src.search.source import iter_chunks_canonical

    texts, it = [], iter_chunks_canonical()
    for rec in it:
        texts.append(rec.get("content") or "")
        if len(texts) >= 1000:
            break
    kiwi = get_kiwi()
    added, rejected = getattr(kiwi, "_dict_stats", (0, 0))
    t0 = time.time()
    out = tokenize_batch(texts)
    dt = time.time() - t0
    rate = len(texts) / dt
    import json
    n_all = json.loads(paths.META.read_text(encoding='utf-8'))['n_rows']
    eta = n_all / rate
    check("6. 1,000건 배치 처리",
          all(isinstance(o, list) for o in out) and len(out) == 1000,
          f"{dt:.1f}초 · 초당 {rate:,.0f}건 · 전량 {n_all:,}건 환산 {eta/60:.1f}분 "
          f"({eta/3600:.2f}시간) · 평균 토큰 {sum(len(o) for o in out)/len(out):.0f}개 "
          f"· 사용자사전 등록 {added}건 거부 {rejected}건 · workers={DEFAULT_WORKERS}")

    print()
    if FAIL:
        print(f"실패 {len(FAIL)}건: {FAIL}")
        return 1
    print("전 항목 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

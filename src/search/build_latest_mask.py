"""is_latest 마스크 생성 — 결정 (c′).

원칙: 정정 재제출된 정기보고서 계열에서 **구본 행은 원칙적으로 빼되,
최신본에 같은 section_path 가 아예 없는 행은 남긴다.**
정정본이 본문만 재제출하고 (첨부)재무제표·감사보고서를 빼고 내는 사례가 많아서,
계열 전체를 빼면(규칙 b) 그 첨부가 코퍼스에서 통째로 사라진다.

계열 = (company, report_nm 에서 선행 [기재정정] 류 접두를 뗀 것), doc_group=='periodic' 한정.
이벤트 공시(major/exchange/holding)는 제목이 같아도 매번 다른 사건이므로 계열로 묶지 않는다
(삼성E&A 「단일판매ㆍ공급계약체결」 87건). 전부 최신본으로 둔다.

section_path 는 0번 원소가 문서 제목이라 [기재정정] 유무로 갈리므로 떼어내고,
각 원소의 공백을 정규화한 튜플로 비교한다.

  python -m src.search.build_latest_mask

산출물: data/index/latest_mask.npz  (data/index/vectors/ 는 읽기 전용이라 그 밖에 쓴다)
  - is_latest   : 검색 대상인가
  - preserved   : 구본인데 최신본에 그 섹션이 없어 남겨둔 행인가
  - series_code : 계열 식별자. 결과 조립 단계에서 "같은 계열의 최신본이 이미 뽑혔으면
                  preserved 를 버린다"(결정 추록 v2 확정 4)를 판정하는 데 쓴다
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from src.index import paths
from src.search.source import iter_chunks_canonical

OUT = paths.ROOT / "data" / "index" / "latest_mask.npz"
MANIFEST = paths.ROOT / "corpus" / "manifest.jsonl"
STATS = paths.REPORTS / "latest_mask.json"

# 「[기재정정]」「[첨부정정]」「[첨부추가]」… 선행 대괄호 접두를 전부 뗀다
_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)+")
_WS = re.compile(r"\s+")


def base_report_nm(report_nm: str) -> str:
    return _WS.sub(" ", _PREFIX.sub("", report_nm or "")).strip()


def norm_section(section_path) -> tuple[str, ...]:
    """문서 제목(0번)을 떼고 공백 정규화한 튜플."""
    return tuple(_WS.sub(" ", str(s)).strip() for s in (section_path or [])[1:])


def main() -> int:
    t0 = time.time()
    idm = pd.read_parquet(paths.ID_MAP)
    n = len(idm)

    man = pd.DataFrame([json.loads(l) for l in open(MANIFEST, encoding="utf-8")])
    meta = man[["rcept_no", "report_nm", "is_correction"]].rename(columns={"rcept_no": "receipt_no"})
    d = idm.merge(meta, on="receipt_no", how="left")
    if d["report_nm"].isna().any():
        raise SystemExit(f"manifest 조인 실패 {int(d['report_nm'].isna().sum()):,}행 — 중단")
    d["base_nm"] = d["report_nm"].map(base_report_nm)
    # 계열 식별자 — periodic 이 아니어도 부여한다. preserved 가 없는 계열이라 조립에 영향이 없고,
    # 배열에 -1 같은 예외값이 섞이지 않아 쓰는 쪽이 단순해진다.
    series_code = pd.factorize(
        pd.Series(list(zip(d["company"].astype(str), d["base_nm"].astype(str))), dtype=object)
    )[0]

    # 계열 판정은 periodic 만
    per = d["doc_group"].to_numpy() == "periodic"
    key = list(zip(d["company"], d["base_nm"]))
    rcpt = d["receipt_no"].to_numpy()

    latest_of: dict[tuple, str] = {}
    for i in np.flatnonzero(per):
        k = key[i]
        if k not in latest_of or rcpt[i] > latest_of[k]:
            latest_of[k] = rcpt[i]

    stale = per & np.array([latest_of.get(key[i]) != rcpt[i] for i in range(n)])
    n_series = sum(1 for k in latest_of if True)
    multi = {k for k in latest_of}  # 계열 중 접수번호가 2개 이상인 것만 따로 센다
    rc_by_key: dict[tuple, set] = defaultdict(set)
    for i in np.flatnonzero(per):
        rc_by_key[key[i]].add(rcpt[i])
    n_multi = sum(1 for k, v in rc_by_key.items() if len(v) > 1)

    # section_path 는 원본 JSONL 에만 있다. 정준 순서로 한 번 훑는다.
    sec = np.empty(n, dtype=object)
    for i, rec in enumerate(iter_chunks_canonical()):
        sec[i] = norm_section(rec.get("section_path"))
    if i + 1 != n:
        raise SystemExit(f"행 수 불일치: 청크 {i+1:,} vs id_map {n:,}")

    # 계열별 최신본 section 집합
    latest_secs: dict[tuple, set] = defaultdict(set)
    for i in np.flatnonzero(per & ~stale):
        latest_secs[key[i]].add(sec[i])

    is_latest = np.ones(n, dtype=bool)
    preserved = np.zeros(n, dtype=bool)
    for i in np.flatnonzero(stale):
        if sec[i] in latest_secs.get(key[i], ()):
            is_latest[i] = False          # 최신본에 같은 섹션이 있다 → 구본 제외
        else:
            preserved[i] = True           # 최신본에 없는 섹션 → 남긴다

    n_stale = int(stale.sum())
    n_excluded = int((~is_latest).sum())
    n_preserved = int(preserved.sum())
    if n_preserved == 0:
        raise SystemExit(
            "보존 행이 0 — section_path 매칭이 과하게 성공했다. 규칙이 (b)로 퇴화한 것이므로 중단한다"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 회귀 검사 기준 (결정 추록 v2 확정 2)
    if n_excluded >= n_stale:
        raise SystemExit(f"제외({n_excluded:,}) 가 구본 총량({n_stale:,}) 이상 — 규칙이 (b)로 퇴화했다")
    if int(is_latest.sum()) != n - n_excluded:
        raise SystemExit("검색 대상 행 수가 (전체 - 제외) 와 다르다")

    np.savez_compressed(
        OUT,
        is_latest=is_latest,
        preserved=preserved,
        series_code=series_code.astype(np.int32),
    )

    stats = {
        "n_rows": n,
        "n_series_periodic": len(rc_by_key),
        "n_series_with_correction": n_multi,
        "n_stale_rows": n_stale,
        "n_excluded": n_excluded,
        "n_preserved": n_preserved,
        "n_searchable": int(is_latest.sum()),
        "n_series_codes": int(series_code.max()) + 1,
        "seconds": round(time.time() - t0, 1),
    }
    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in stats.items():
        print(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
    print(f"[latest_mask] {OUT.relative_to(paths.ROOT)} 기록")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

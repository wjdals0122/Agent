"""회사명 해석. 사람이 쓰는 이름 → (corp_code, stock_code).

【치명 A】 두 코드 체계가 이름만 같고 값이 다르다.
  - universe.csv 의 corp_code   : 8자리 DART 고유번호 (00126380)
  - id_map.parquet 의 corp_code : 실제로는 6자리 종목코드 (005930)
섞어 쓰면 회사 필터가 항상 0건이 되는데 예외도 경고도 나지 않는다.
그래서 resolve_company 는 둘 다 반환하고, **검색 필터에는 stock_code 만 쓴다.**

  python -m src.search.aliases --build     # config/aliases.yaml 생성
  python -m src.search.aliases 하이닉스      # 조회
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "corpus" / "universe.csv"
ALIASES_YAML = ROOT / "config" / "aliases.yaml"

# 법인격 표기. 이름의 일부가 아니므로 비교 전에 떼어낸다.
_KO_SUFFIX = ("주식회사", "(주)", "（주）", "㈜", "(유)", "유한회사")
_EN_SUFFIX = {
    "co", "co.", "ltd", "ltd.", "inc", "inc.", "corp", "corp.",
    "corporation", "company", "limited", "incorporation", "holdings" ,
}
_PUNCT = re.compile(r"[\s.,·''\"“”\-–—/\()\[\]]+")


def normalize(name: str) -> str:
    """비교용 표준형. 법인격·구두점·공백·대소문자 차이를 지운다.

    '(주)삼성전자' '삼성전자주식회사' '삼성 전자' → 모두 '삼성전자'
    'Alteogen Inc.' → 'alteogen'  (Inc. 를 남기면 'NC' 질의가 여기에 걸린다)
    """
    s = str(name or "").strip()
    for suf in _KO_SUFFIX:
        s = s.replace(suf, "")
    # 영문 법인격은 낱말 단위로만 떼어낸다 ('company' 를 문자열로 지우면 이름을 깎는다)
    toks = [t for t in re.split(r"\s+", s) if t]
    while toks and toks[-1].lower().strip(".,") in _EN_SUFFIX:
        toks.pop()
    s = " ".join(toks)
    s = _PUNCT.sub("", s)
    return s.lower()


# ---------------------------------------------------------------- 사전 만들기

def build(out: Path = ALIASES_YAML) -> dict:
    """청크의 aliases 필드 + universe.csv 의 3개 이름 칸을 합집합으로 모은다.

    없는 별칭을 지어내지 않는다. 데이터에 있는 것만 담는다.
    """
    import pandas as pd

    from src.search.source import iter_chunks_canonical

    uni = pd.read_csv(UNIVERSE, dtype=str).fillna("")
    from_chunks: dict[str, set[str]] = defaultdict(set)
    n = 0
    for rec in iter_chunks_canonical():
        n += 1
        comp = rec.get("company")
        for a in rec.get("aliases") or []:
            a = str(a).strip()
            if a:
                from_chunks[comp].add(a)
    print(f"  청크 {n:,}행에서 회사 {len(from_chunks)}곳의 별칭 수집", flush=True)

    companies = []
    for _, r in uni.iterrows():
        names = {r["corp_name"], r["listed_name"], r["corp_eng_name"]}
        # 청크의 company 는 universe 의 corp_name 또는 listed_name 과 붙는다
        for k in (r["corp_name"], r["listed_name"]):
            names |= from_chunks.get(k, set())
        names = sorted({x.strip() for x in names if x and x.strip()})
        companies.append({
            "corp_code": r["corp_code"],       # 8자리 DART 고유번호
            "stock_code": r["stock_code"],     # 6자리 종목코드 — 검색 필터는 이것만 쓴다
            "corp_name": r["corp_name"],
            "listed_name": r["listed_name"],
            "corp_eng_name": r["corp_eng_name"],
            "aliases": names,
        })

    doc = {
        "_note": "자동 생성. python -m src.search.aliases --build 로 다시 만든다. "
                 "데이터에 없는 별칭을 손으로 추가하지 말 것.",
        "companies": companies,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False, width=200)
    total = sum(len(c["aliases"]) for c in companies)
    print(f"[aliases] {out.relative_to(ROOT)} 기록 — {len(companies)}개사 / 별칭 {total:,}개")
    return doc


# ---------------------------------------------------------------- 조회

@lru_cache(maxsize=1)
def _load() -> tuple[list[dict], dict[str, list[int]], list[tuple[str, int]]]:
    if not ALIASES_YAML.is_file():
        raise FileNotFoundError(
            f"{ALIASES_YAML} 가 없다. 먼저 python -m src.search.aliases --build"
        )
    doc = yaml.safe_load(ALIASES_YAML.read_text(encoding="utf-8"))
    comps = doc["companies"]
    exact: dict[str, list[int]] = defaultdict(list)
    flat: list[tuple[str, int]] = []
    for i, c in enumerate(comps):
        for a in c["aliases"]:
            k = normalize(a)
            if not k:
                continue
            if i not in exact[k]:
                exact[k].append(i)
            flat.append((k, i))
    return comps, dict(exact), flat


def _card(c: dict, matched: str = "") -> dict:
    out = {
        "corp_code": c["corp_code"],
        "stock_code": c["stock_code"],
        "corp_name": c["corp_name"],
        "listed_name": c["listed_name"],
    }
    if matched:
        out["matched"] = matched
    return out


def resolve_company(name: str) -> dict:
    """이름 하나를 회사로 해석한다. 확실하지 않으면 하나를 고르지 않는다.

    status="ok"        : 한 곳으로 확정. corp_code(8) / stock_code(6) 둘 다 반환
    status="ambiguous" : 후보 2곳 이상. candidates 로 돌려주고 고르지 않는다
    status="not_found" : 코퍼스 70개사 밖
    """
    comps, exact, flat = _load()
    q = normalize(name)
    if not q:
        return {"status": "not_found", "query": name, "note": "빈 이름"}

    # 1) 완전 일치가 있으면 그것만 본다
    hit = exact.get(q, [])
    if len(hit) == 1:
        return {"status": "ok", "query": name, **_card(comps[hit[0]], q)}
    if len(hit) > 1:
        return {"status": "ambiguous", "query": name,
                "candidates": [_card(comps[i]) for i in hit],
                "note": f"같은 이름을 쓰는 회사 {len(hit)}곳"}

    # 2) 부분 일치 — 질의가 별칭 안에 들어 있는 경우 ('하이닉스' ⊂ 'SK하이닉스')
    part = sorted({i for k, i in flat if len(q) >= 2 and q in k})
    if len(part) == 1:
        return {"status": "ok", "query": name, **_card(comps[part[0]], q),
                "note": "부분 일치"}
    if len(part) > 1:
        return {"status": "ambiguous", "query": name,
                "candidates": [_card(comps[i]) for i in part],
                "note": f"부분 일치 후보 {len(part)}곳 — 더 구체적인 이름이 필요하다"}

    # 3) 반대 방향 — 별칭이 질의 안에 들어 있는 경우 ('삼성전자 반도체' → 삼성전자)
    rev = sorted({i for k, i in flat if len(k) >= 3 and k in q})
    if len(rev) == 1:
        return {"status": "ok", "query": name, **_card(comps[rev[0]], q),
                "note": "질의에 회사명이 포함됨"}
    if len(rev) > 1:
        return {"status": "ambiguous", "query": name,
                "candidates": [_card(comps[i]) for i in rev],
                "note": f"질의에 여러 회사명이 포함됨 ({len(rev)}곳)"}

    return {"status": "not_found", "query": name, "note": "코퍼스 70개사에 없다"}


def all_companies() -> list[dict]:
    return _load()[0]


# 사전 등록에서 뺄 것 (2026-08-30 검수로 확정)
#  A. 법인격 표기 포함 258개 — '에이치디현대중공업(주)' 같은 통짜 토큰이 되어
#     정작 '현대중공업' 질의와 매칭되지 않는다.
#  B. 한글 3자 이하 8개 — Kiwi 가 이미 전부 한 덩어리로 처리하므로 등록 이득이 없고,
#     '에스엠' 은 무관한 '와이에스엠씨제일차' 를 부순다.
# aliases.yaml 자체는 429개를 그대로 둔다. resolve_company 는 전량을 계속 쓴다.
_LEGAL = re.compile(r"\(주\)|（주）|㈜|주식회사|\(유\)|유한회사")
_HANGUL = re.compile(r"[가-힣]")

# Kiwi 가 쪼개버리는 이름들 — 사전에서 빠지면 검색이 깨진다.
DICT_MUST_KEEP = ("LIG넥스원", "한전기술", "시프트업", "파마리서치", "세아베스틸지주")


def _dict_excluded(a: str) -> bool:
    if _LEGAL.search(a):
        return True
    return bool(_HANGUL.search(a)) and len(re.sub(r"\s", "", a)) <= 3


def all_aliases() -> list[str]:
    """**Kiwi 사용자사전에 넣을** 별칭. 조회용 전량과 다르다 (아래 필터 참조)."""
    out: set[str] = set()
    for c in _load()[0]:
        out |= set(c["aliases"])
        out |= {c["corp_name"], c["listed_name"]}
    kept = sorted(x for x in out if x and not _dict_excluded(x))
    missing = [w for w in DICT_MUST_KEEP if w not in kept]
    assert not missing, f"사전에서 빠지면 안 되는 이름이 빠졌다: {missing}"
    return kept


def all_aliases_full() -> list[str]:
    """조회용 전량 (필터 전). 사전 등록에는 쓰지 않는다."""
    out: set[str] = set()
    for c in _load()[0]:
        out |= set(c["aliases"])
        out |= {c["corp_name"], c["listed_name"]}
    return sorted(x for x in out if x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="*")
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    if args.build:
        build()
        return 0
    if not args.name:
        ap.error("이름을 주거나 --build 를 쓴다")
    import json
    for nm in args.name:
        print(json.dumps(resolve_company(nm), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

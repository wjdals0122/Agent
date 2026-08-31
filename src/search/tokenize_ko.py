"""한국어 토큰화. BM25 색인과 질의가 **같은 함수**를 써야 한다.

【중요 — 사용자사전은 색인 만들기 "전"에 확정돼야 한다】
Kiwi 사용자사전에 회사명을 넣어야 'LIG넥스원' 이 한 덩어리로 남는다.
색인을 만든 뒤에 단어를 추가하면 색인에는 반영되지 않으므로 전량 재색인이 필요하다.
그래서 사전 구성(=config/aliases.yaml)이 확정된 뒤에 1-B 로 넘어간다.

【숫자】
형태소 분석기는 '3,908,761' 을 쪼갠다. 공시 질의는 금액 자체가 검색어인 경우가 많아서
원문에서 숫자를 정규식으로 한 번 더 긁어 콤마 있는 형태와 없는 형태를 둘 다 넣는다.

  python -m src.search.tokenize_ko "LIG넥스원의 2024년 유형자산 취득액은 3,908,761백만원이다"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from functools import lru_cache

# 내용어만 남긴다. 조사·어미·기호는 BM25 에 잡음만 더한다.
# 비교는 반드시 tag.split("-")[0] 로 한다 — 불규칙 활용은 'VV-I' / 'VV-R' 로 나온다.
KEEP = {
    "NNG", "NNP", "NNB", "NR", "NP",   # 명사류
    "VV", "VA", "XR",                  # 용언 어간 · 어근
    "SL", "SH", "SN",                  # 외국어 · 한자 · 숫자
}

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WS = re.compile(r"\s+")

DEFAULT_WORKERS = min(8, (os.cpu_count() or 4))


@lru_cache(maxsize=4)
def get_kiwi(num_workers: int = DEFAULT_WORKERS):
    """사용자사전을 얹은 Kiwi 인스턴스. 프로세스당 한 번만 만든다."""
    from kiwipiepy import Kiwi

    from src.search.aliases import all_aliases

    kiwi = Kiwi(num_workers=num_workers)
    added = rejected = 0
    for w in all_aliases():
        forms = {w}
        # 공백이 든 별칭('JYP Ent', 'LS ELECTRIC')은 등록이 거부될 수 있다.
        # 공백 제거형을 함께 넣어 최소한 붙여 쓴 표기는 한 덩어리로 잡히게 한다.
        nows = _WS.sub("", w)
        if nows != w:
            forms.add(nows)
        for f in forms:
            if len(f) < 2:
                continue
            try:
                kiwi.add_user_word(f, "NNP", 5.0)
                added += 1
            except Exception:
                rejected += 1
    kiwi._dict_stats = (added, rejected)  # 보고용
    return kiwi


def _numbers(text: str) -> list[str]:
    out = []
    for m in _NUM.finditer(text or ""):
        raw = m.group(0).rstrip(",")
        out.append(raw.lower())
        bare = raw.replace(",", "")
        if bare and bare != raw:
            out.append(bare)
    return out


def _from_tokens(tokens, text: str) -> list[str]:
    out = [t.form.lower() for t in tokens if t.tag.split("-")[0] in KEEP]
    # 형태소 분석기가 이미 같은 문자열을 내놨으면 다시 넣지 않는다.
    # 그냥 붙이면 같은 숫자의 등장 횟수가 부풀려져 BM25 점수가 왜곡된다.
    seen = set(out)
    for num in _numbers(text):
        if num not in seen:
            out.append(num)
            seen.add(num)
    return out


def tokenize(text: str, num_workers: int = DEFAULT_WORKERS) -> list[str]:
    return _from_tokens(get_kiwi(num_workers).tokenize(text or ""), text or "")


def tokenize_batch(texts, num_workers: int = DEFAULT_WORKERS) -> list[list[str]]:
    """리스트를 **통째로** Kiwi 에 넘긴다.

    for 루프 안에서 tokenize 를 한 건씩 부르면 num_workers 가 아무 일도 하지 않는다.
    """
    texts = [t or "" for t in texts]
    kiwi = get_kiwi(num_workers)
    return [_from_tokens(toks, txt) for toks, txt in zip(kiwi.tokenize(texts), texts)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="+")
    args = ap.parse_args()
    for t in args.text:
        print(tokenize(t))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

"""BM25 조회. 색인은 이미 가중치가 곱해진 CSC 이므로 열 슬라이스 합산만 한다.

  from src.search.bm25 import BM25
  bm = BM25()
  s = bm.score("연결재무제표 매출액")        # 원점수 (정규화하지 않는다)
  s = bm.score("매출액", rows=cand_rows)     # 하드필터 후보 안에서만
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import scipy.sparse as sp

from src.index import paths
from src.search.tokenize_ko import tokenize

DIR = paths.ROOT / "data" / "index" / "bm25"


class BM25:
    def __init__(self, dir: Path = DIR) -> None:
        if not (dir / "bm25_csc.npz").is_file():
            raise FileNotFoundError(
                f"{dir}/bm25_csc.npz 가 없다. 먼저 python -m src.search.bm25_build"
            )
        self.dir = dir
        self.meta = json.loads((dir / "meta.json").read_text(encoding="utf-8"))
        m = sp.load_npz(dir / "bm25_csc.npz")
        assert sp.isspmatrix_csc(m), "CSC 가 아니다"
        self.n, self.v = m.shape
        # 열 슬라이싱은 indptr/indices/data 를 직접 자른다.
        # getcol() 은 호출마다 행렬 객체를 새로 만들고 scipy 정리 대상 API 다.
        self.indptr = m.indptr
        self.indices = m.indices
        self.data = m.data
        terms = (dir / "vocab.txt").read_text(encoding="utf-8").split("\n")
        self.vocab = {t: i for i, t in enumerate(terms)}

    def term_ids(self, query: str) -> dict[int, int]:
        """질의어 → {term_id: 질의 내 등장횟수}. 사전에 없는 말은 버린다."""
        out: dict[int, int] = {}
        for w in tokenize(query or ""):
            j = self.vocab.get(w)
            if j is not None:
                out[j] = out.get(j, 0) + 1
        return out

    def score(self, query: str, rows: np.ndarray | None = None) -> np.ndarray:
        """BM25 원점수. rows 를 주면 그 행들에 대한 점수만 같은 순서로 돌려준다.

        점수 0 은 '질의어를 하나도 포함하지 않음' 을 뜻한다.
        정규화하지 않는다 — 답 없는 질문을 거르는 임계값 판단에 원점수가 필요하다.
        """
        acc = np.zeros(self.n, dtype=np.float32)
        for j, qf in self.term_ids(query).items():
            s, e = self.indptr[j], self.indptr[j + 1]
            if s == e:
                continue
            acc[self.indices[s:e]] += self.data[s:e] * qf
        return acc if rows is None else acc[rows]

    def top_k(self, query: str, k: int = 10,
              rows: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(행 번호, 점수) 를 점수 내림차순으로. 점수 0 인 행은 제외한다."""
        s = self.score(query, rows)
        nz = np.flatnonzero(s > 0)
        if nz.size == 0:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)
        k = min(k, nz.size)
        part = nz[np.argpartition(-s[nz], k - 1)[:k]]
        order = part[np.argsort(-s[part])]
        return (order if rows is None else np.asarray(rows)[order]), s[order]


@lru_cache(maxsize=1)
def get_bm25() -> BM25:
    return BM25()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args()
    bm = BM25()
    for q in args.query:
        rows, s = bm.top_k(q, args.k)
        print(f"\n=== {q!r} ===")
        for r, v in zip(rows, s):
            print(f"  row={r} score={v:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

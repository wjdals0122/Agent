"""BM25 색인 생성. scipy CSC 로 직접 만든다.

외부 BM25 라이브러리(bm25s / rank_bm25 / elasticsearch)를 쓰지 않는 이유 —
이 검색기는 하드필터(회사·연도)를 **먼저** 걸고 그 후보 안에서만 점수를 낸다.
라이브러리들은 전역 top-k 를 전제하므로 회사 하나로 좁히면 0건이 된다.

【문서 가중치를 색인 시점에 미리 곱해둔다】
  W[d,t] = idf(t) · f(d,t)·(k1+1) / ( f(d,t) + k1·(1 − b + b·len(d)/avglen) )
  idf(t) = ln( 1 + (N − df + 0.5)/(df + 0.5) )
검색 때는 질의어에 해당하는 **열만 슬라이스해서 더하면** 끝난다.

  python -m src.search.bm25_build --limit 1000    # 스모크
  python -m src.search.bm25_build                 # 전량
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from array import array
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import scipy.sparse as sp

from src.index import paths
from src.search.source import iter_chunks_canonical
from src.search.tokenize_ko import tokenize_batch

OUT = paths.ROOT / "data" / "index" / "bm25"
K1 = 1.5
B = 0.75
BATCH = 2000


def bm25_text(c: dict) -> str:
    """색인 대상 문자열. 본문은 embedding_text 가 아니라 content 를 쓴다.

    필드명은 실제 청크 스키마 기준:
      company / document_title / section_path(list) / content
    """
    return " ".join((
        c.get("company") or "",
        c.get("document_title") or "",
        " > ".join(c.get("section_path") or []),
        c.get("content") or "",
    ))


def verify_row_order(limit: int | None = None) -> int:
    """색인 만들기 전에 행 순서 계약을 다시 확인한다."""
    import pyarrow.parquet as pq

    t = pq.read_table(paths.ID_MAP, columns=["chunk_id", "embed_sha1"])
    ids = t.column("chunk_id").to_pylist()
    shas = t.column("embed_sha1").to_pylist()
    bad_sha = bad_id = row = 0
    for rec in iter_chunks_canonical():
        if limit and row >= limit:
            break
        if hashlib.sha1(rec["embedding_text"].encode()).hexdigest()[:16] != shas[row]:
            bad_sha += 1
        if rec["chunk_id"] != ids[row]:
            bad_id += 1
        row += 1
    if bad_sha or bad_id:
        raise SystemExit(f"행 정렬 검증 실패 — embed_sha1 {bad_sha}건 / chunk_id {bad_id}건")
    if limit is None and row != len(ids):
        raise SystemExit(f"행 수 불일치: 청크 {row:,} vs id_map {len(ids):,}")
    print(f"  [정렬검증] {row:,}행 전량 일치 (embed_sha1 · chunk_id)", flush=True)
    return row


def build(limit: int | None = None, out: Path = OUT) -> dict:
    import psutil

    proc = psutil.Process(os.getpid())
    rss0 = proc.memory_info().rss
    t_all = time.time()

    t_v = time.time()
    n_expected = verify_row_order(limit)
    t_verify = time.time() - t_v

    # Kiwi 모델 적재를 타이밍에서 빼야 전량 환산이 맞는다
    t_w = time.time()
    from src.search.tokenize_ko import get_kiwi
    get_kiwi()
    t_warm = time.time() - t_w
    t0 = time.time()

    vocab: dict[str, int] = {}
    # 파이썬 list 로 쌓으면 nnz 9천만에서 수 GB 를 낭비한다. array.array 를 쓴다.
    indices = array("i")
    data = array("f")
    indptr = array("q", [0])
    doc_len = array("f")

    n = 0
    peak = rss0
    buf: list[str] = []

    def flush(buf: list[str]) -> None:
        nonlocal n, peak
        for toks in tokenize_batch(buf):
            counts: dict[int, int] = {}
            for w in toks:
                j = vocab.get(w)
                if j is None:
                    j = vocab[w] = len(vocab)
                counts[j] = counts.get(j, 0) + 1
            for j, f in counts.items():
                indices.append(j)
                data.append(f)
            indptr.append(len(indices))
            doc_len.append(float(len(toks)))
            n += 1
        peak = max(peak, proc.memory_info().rss)

    for rec in iter_chunks_canonical():
        buf.append(bm25_text(rec))
        if len(buf) >= BATCH or (limit and n + len(buf) >= limit):
            flush(buf)
            buf = []
            if limit and n >= limit:
                break
            if n % 50_000 == 0:
                print(f"  {n:,}행 · {time.time()-t0:.0f}s · vocab {len(vocab):,} "
                      f"· nnz {len(indices):,}", flush=True)
    if buf:
        flush(buf)

    if n != n_expected:
        raise SystemExit(f"토큰화 행 수 {n:,} != 검증 행 수 {n_expected:,}")

    t_tok = time.time() - t0
    t_mat = time.time()
    V = len(vocab)
    nnz = len(indices)

    ind = np.frombuffer(indices, dtype=np.int32)
    dat = np.frombuffer(data, dtype=np.float32).astype(np.float32).copy()
    ptr = np.frombuffer(indptr, dtype=np.int64)
    dl = np.frombuffer(doc_len, dtype=np.float32)
    avglen = float(dl.mean()) if n else 0.0

    # df(t) — 행마다 term 은 한 번만 들어가므로 등장 횟수가 곧 문서빈도다
    df = np.bincount(ind, minlength=V).astype(np.float64)
    idf = np.log(1.0 + (n - df + 0.5) / (df + 0.5)).astype(np.float32)

    # 문서 길이를 nnz 축으로 펼쳐 한 번에 계산한다
    dl_nnz = np.repeat(dl, np.diff(ptr))
    denom = dat + K1 * (1.0 - B + B * dl_nnz / (avglen or 1.0))
    W = idf[ind] * (dat * (K1 + 1.0) / denom)

    csr = sp.csr_matrix((W.astype(np.float32), ind, ptr), shape=(n, V))
    csc = csr.tocsc()
    del csr
    peak = max(peak, proc.memory_info().rss)

    out.mkdir(parents=True, exist_ok=True)
    sp.save_npz(out / "bm25_csc.npz", csc, compressed=False)
    (out / "vocab.txt").write_text(
        "\n".join(sorted(vocab, key=vocab.get)), encoding="utf-8")
    np.save(out / "doc_len.npy", dl)
    meta = {
        "n_rows": n,
        "verify_seconds": round(t_verify, 1),
        "kiwi_load_seconds": round(t_warm, 1),
        "matrix_seconds": round(time.time() - t_mat, 1),
        "rows_per_sec": round(n / t_tok, 1) if t_tok else None,
        "vocab_size": V,
        "nnz": int(nnz),
        "k1": K1,
        "b": B,
        "avg_doc_len": avglen,
        "limit": limit,
        "tokenize_seconds": round(t_tok, 1),
        "total_seconds": round(time.time() - t_all, 1),
        "peak_rss_gb": round(peak / 1e9, 2),
        "rss_delta_gb": round((peak - rss0) / 1e9, 2),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            "bm25_csc.npz": os.path.getsize(out / "bm25_csc.npz"),
            "vocab.txt": os.path.getsize(out / "vocab.txt"),
            "doc_len.npy": os.path.getsize(out / "doc_len.npy"),
        },
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    for k, v in meta.items():
        print(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
    print(f"[bm25] {out} 기록")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="스모크용 행 수")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else OUT
    build(args.limit, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

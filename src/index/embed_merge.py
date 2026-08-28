"""샤드 4개를 dense.f32.npy + sparse.npz로 합친다. _work/는 지우지 않는다.

  python -m src.index.embed_merge --world 4
"""
from __future__ import annotations

import argparse
import json
import pickle
import time

import numpy as np
import pyarrow.parquet as pq
import scipy.sparse as sp
from numpy.lib.format import open_memmap

from src.index import paths


def load_sparse_shard(path):
    """pickle 스트림(배치별 (row, {tid: w}) 리스트)을 순회한다."""
    with open(path, "rb") as fh:
        while True:
            try:
                recs = pickle.load(fh)
            except (EOFError, pickle.UnpicklingError, ValueError):
                return
            yield from recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", type=int, default=4)
    args = ap.parse_args()

    n_total = pq.read_metadata(paths.EMBED_TEXTS).num_rows
    world = args.world
    bounds = [(r * n_total // world, (r + 1) * n_total // world) for r in range(world)]

    shards = [paths.WORK / f"dense.shard{r}.npy" for r in range(world)]
    missing = [p.name for p in shards if not p.exists()]
    if missing:
        print(f"[merge] 샤드 없음: {missing}")
        return 2

    t0 = time.time()
    # dense: memmap → memmap 복사. 전체를 램에 올리지 않는다.
    out = open_memmap(paths.DENSE, mode="w+", dtype=np.float32, shape=(n_total, paths.DIM))
    for r, (s, e) in enumerate(bounds):
        src = open_memmap(shards[r], mode="r")
        if src.shape != (e - s, paths.DIM):
            print(f"[merge] shard{r} shape {src.shape} != {(e - s, paths.DIM)}")
            return 2
        step = 50_000
        for i in range(0, e - s, step):
            j = min(i + step, e - s)
            out[s + i : s + j] = src[i:j]
        del src
        print(f"[merge] dense shard{r} → rows [{s:,}, {e:,}) ({time.time() - t0:.1f}s)", flush=True)
    out.flush()
    del out
    print(f"[merge] {paths.DENSE.name} 기록 — {n_total:,}×{paths.DIM}", flush=True)

    # sparse: CSR (N, 250002)
    indptr = np.zeros(n_total + 1, dtype=np.int64)
    rows_seen = np.zeros(n_total, dtype=bool)
    per_row: dict[int, dict[int, float]] = {}
    for r in range(world):
        path = paths.WORK / f"sparse.shard{r}.pkl"
        if not path.exists():
            print(f"[merge] {path.name} 없음")
            return 2
        n = 0
        for row, weights in load_sparse_shard(path):
            per_row[row] = weights  # 재실행으로 중복 기록됐다면 마지막 것을 쓴다
            n += 1
        print(f"[merge] sparse shard{r} 레코드 {n:,}건 (누적 고유 {len(per_row):,})", flush=True)

    nnz_total = sum(len(w) for w in per_row.values())
    indices = np.zeros(nnz_total, dtype=np.int32)
    data = np.zeros(nnz_total, dtype=np.float32)
    pos = 0
    for row in range(n_total):
        w = per_row.get(row)
        if w:
            ks = np.fromiter(w.keys(), dtype=np.int32, count=len(w))
            vs = np.fromiter(w.values(), dtype=np.float32, count=len(w))
            order = np.argsort(ks)
            indices[pos : pos + len(w)] = ks[order]
            data[pos : pos + len(w)] = vs[order]
            pos += len(w)
            rows_seen[row] = True
        indptr[row + 1] = pos

    csr = sp.csr_matrix((data, indices, indptr), shape=(n_total, paths.SPARSE_VOCAB))
    sp.save_npz(paths.SPARSE, csr)
    print(
        f"[merge] {paths.SPARSE.name} 기록 — nnz {nnz_total:,}, "
        f"빈 행 {int((~rows_seen).sum()):,}건 ({time.time() - t0:.1f}s)",
        flush=True,
    )

    # meta 갱신: 워커 stats 합산
    meta = json.loads(paths.META.read_text(encoding="utf-8"))
    stats = []
    for r in range(world):
        p = paths.WORK / f"stats.{r}.json"
        if p.exists():
            stats.append(json.loads(p.read_text(encoding="utf-8")))
    if stats:
        meta["max_length"] = stats[0]["max_length"]
        meta["workers"] = stats
        meta["wall_seconds_max_worker"] = max(s["seconds"] for s in stats)
        meta["total_chunks_per_sec"] = round(sum(s["chunks_per_sec"] or 0 for s in stats), 2)
        meta["n_skipped"] = sum(s["n_skipped"] for s in stats)
    meta["merge_seconds"] = round(time.time() - t0, 1)
    meta["sparse_nnz"] = int(nnz_total)
    paths.META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[merge] 완료 {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

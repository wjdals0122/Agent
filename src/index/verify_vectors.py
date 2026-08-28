"""인덱스 검증. 하나라도 실패하면 비영 종료 코드로 끝난다.

  python -m src.index.verify_vectors [--sample 20]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import pyarrow.parquet as pq
import scipy.sparse as sp

from src.index import paths

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20, help="재임베딩 대조할 무작위 행 수")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    t0 = time.time()
    meta = json.loads(paths.META.read_text(encoding="utf-8"))
    n = meta["n_rows"]

    dense = np.load(paths.DENSE, mmap_mode="r")
    check("dense shape", dense.shape == (n, paths.DIM), f"{dense.shape} (기대 {(n, paths.DIM)})")

    # 대용량이므로 블록 단위로 훑는다
    n_nan = n_zero = 0
    norm_min, norm_max = np.inf, -np.inf
    step = 50_000
    for i in range(0, n, step):
        blk = np.asarray(dense[i : i + step], dtype=np.float32)
        n_nan += int((~np.isfinite(blk)).any(axis=1).sum())
        norms = np.linalg.norm(blk, axis=1)
        n_zero += int((norms < 1e-6).sum())
        norm_min = min(norm_min, float(norms.min()))
        norm_max = max(norm_max, float(norms.max()))

    check("NaN/Inf 0건", n_nan == 0, f"{n_nan}건")
    check("영벡터 0건", n_zero == 0, f"{n_zero}건")
    check(
        "L2 norm 1.0 ± 1e-3",
        abs(norm_min - 1.0) <= 1e-3 and abs(norm_max - 1.0) <= 1e-3,
        f"min={norm_min:.6f} max={norm_max:.6f}",
    )

    idm = pq.read_table(paths.ID_MAP)
    check("id_map 행 수", idm.num_rows == n, f"{idm.num_rows:,}")

    # embed_sha1 전량 재계산 대조 (소스 재파싱 대신 prepare가 떨군 embed_texts를 쓰지 않고
    # 원본 JSONL에서 다시 읽는다 — prepare 단계의 실수까지 잡기 위해서다)
    import orjson

    stored = idm.column("embed_sha1").to_pylist()
    stored_ids = idm.column("chunk_id").to_pylist()
    mismatch = id_mismatch = 0
    row = 0
    limit = meta.get("limit")
    # prepare 와 **같은 규칙으로** 걸러야 row 가 맞는다. 안 거르면 대체된 옛 문서만큼
    # 밀려서 stored[row] 가 인덱스 범위를 넘는다.
    skip_docs = set(meta.get("skipped_doc_ids") or ()) or paths.skipped_doc_ids()
    for path in paths.chunk_files():
        if limit and row >= limit:
            break
        with open(path, "rb") as fh:
            for raw in fh:
                if limit and row >= limit:
                    break
                if not raw.strip():
                    continue
                rec = orjson.loads(raw)
                if rec.get("doc_id") in skip_docs:
                    continue
                if hashlib.sha1(rec["embedding_text"].encode()).hexdigest()[:16] != stored[row]:
                    mismatch += 1
                if rec["chunk_id"] != stored_ids[row]:
                    id_mismatch += 1
                row += 1
    check("embed_sha1 전량 일치", mismatch == 0 and row == n, f"불일치 {mismatch}건 / 대조 {row:,}행")
    check("chunk_id 순서 일치", id_mismatch == 0, f"불일치 {id_mismatch}건")

    csr = sp.load_npz(paths.SPARSE)
    empty = int((np.diff(csr.indptr) == 0).sum())
    check("sparse shape", csr.shape == (n, paths.SPARSE_VOCAB), f"{csr.shape}")
    check("sparse 빈 행 0건", empty == 0, f"{empty}건, nnz={csr.nnz:,}")

    # 무작위 표본 재임베딩 대조
    rng = np.random.default_rng(args.seed)
    sample = sorted(rng.choice(n, size=min(args.sample, n), replace=False).tolist())
    texts = pq.read_table(paths.EMBED_TEXTS, columns=["embed_text"]).column("embed_text").to_pylist()
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel(paths.MODEL_NAME, use_fp16=True, normalize_embeddings=True, devices="cuda:0")
    out = model.encode(
        [texts[i] for i in sample],
        batch_size=len(sample),
        max_length=meta.get("max_length") or 1024,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    re_vecs = np.asarray(out["dense_vecs"], dtype=np.float32)
    stored_vecs = np.asarray(dense[sample], dtype=np.float32)
    cos = (re_vecs * stored_vecs).sum(axis=1)
    check("재임베딩 코사인 > 0.999", bool((cos > 0.999).all()), f"min={cos.min():.6f} mean={cos.mean():.6f}")

    n_fail = sum(1 for _, ok, _ in CHECKS if not ok)

    # 리포트
    w = meta.get("workers", [])
    lines = [
        "# 벡터 인덱스 검증 결과",
        "",
        f"- 생성 시각: {datetime.now(timezone.utc).isoformat()}",
        f"- 모델: `{meta['model']}` / dim={meta['dim']} / max_length={meta.get('max_length')}",
        f"- 행 수: {n:,}",
        f"- 결과: **{'전 항목 통과' if n_fail == 0 else f'{n_fail}개 항목 실패'}**",
        "",
        "## 검사 항목",
        "",
        "| 항목 | 결과 | 상세 |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {nm} | {'PASS' if ok else 'FAIL'} | {d} |" for nm, ok, d in CHECKS]

    if w:
        lines += [
            "",
            "## 처리 시간",
            "",
            "| rank | rows | 건수 | 소요 | chunk/s | GPU peak | 스킵 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in w:
            lines.append(
                f"| {s['rank']} | {s['rows'][0]:,}–{s['rows'][1]:,} | {s['n_encoded']:,} | "
                f"{s['seconds']/60:.1f}분 | {s['chunks_per_sec']} | {s['gpu_peak_gb']}GB | {s['n_skipped']} |"
            )
        wall = meta.get("wall_seconds_max_worker", 0)
        lines += [
            "",
            f"- 벽시계 소요(가장 늦은 워커): **{wall/60:.1f}분** ({wall/3600:.2f}시간)",
            f"- 4-GPU 합산 처리량: **{meta.get('total_chunks_per_sec')} chunk/s**",
            f"- 머지 소요: {meta.get('merge_seconds')}초",
            f"- 스킵된 행: {meta.get('n_skipped', 0)}건",
        ]
    lines += ["", f"- 검증 소요: {time.time() - t0:.1f}초", ""]

    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    (paths.REPORTS / "validation_vectors.md").write_text("\n".join(lines), encoding="utf-8")
    rel = (paths.REPORTS / "validation_vectors.md").relative_to(paths.ROOT)
    print(f"\n[verify] {rel} 기록 — 실패 {n_fail}건", flush=True)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""임베딩 건강검진 — 셀프 리트리벌 + 결측/정합성.

지시서가 지목한 eval/step0_verify_embeddings.py 가 리포에 없어 같은 목적으로 새로 쓴 것.
행 수 정합, corp_code/doc_group 결측, 셀프 리트리벌 top-1 적중률을 본다.
재임베딩 코사인·norm·sparse 검사는 이미 src/index/verify_vectors.py 가 한다(중복 안 함).

  python eval/embed_health_check.py --sample 200
"""
from __future__ import annotations

import argparse, hashlib, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "data" / "index" / "vectors"
CHUNKS_DIR = ROOT / "data" / "processed" / "chunks_by_10_companies"
REPORT = ROOT / "reports" / "embed_health.md"
MODEL_NAME = "BAAI/bge-m3"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}", flush=True)
    return ok


def read_records(rows: list[int], file_idx, offset, files) -> list[dict]:
    """text_offsets 의 (file_idx, byte offset) 로 원본 JSONL 행을 직접 읽는다."""
    out: dict[int, dict] = {}
    handles: dict[int, object] = {}
    try:
        for r in rows:
            fi = int(file_idx[r])
            fh = handles.get(fi) or handles.setdefault(fi, open(CHUNKS_DIR / files[fi], "rb"))
            fh.seek(int(offset[r]))
            out[r] = json.loads(fh.readline())
    finally:
        for fh in handles.values():
            fh.close()
    return [out[r] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cuda:0")
    args = ap.parse_args()
    t0 = time.time()

    meta = json.loads((VECTORS / "meta.json").read_text(encoding="utf-8"))
    n = meta["n_rows"]
    dense = np.load(VECTORS / "dense.f32.npy", mmap_mode="r")
    idm = pd.read_parquet(VECTORS / "id_map.parquet")
    tof = np.load(VECTORS / "text_offsets.npz")

    # 1. 행 수 정합
    ok_rows = dense.shape[0] == len(idm) == n == len(tof["file_idx"])
    check("행 수 일치 (dense / id_map / meta / text_offsets)", ok_rows,
          f"dense={dense.shape[0]:,} id_map={len(idm):,} meta={n:,} offsets={len(tof['file_idx']):,}")

    # 2. 결측
    for col in ("corp_code", "doc_group"):
        s = idm[col].astype("string")
        miss = int(s.isna().sum() + (s.fillna("").str.strip() == "").sum())
        check(f"{col} 결측 0건", miss == 0, f"{miss}건")

    # 3. 셀프 리트리벌
    rng = np.random.default_rng(args.seed)
    sample = sorted(rng.choice(n, size=min(args.sample, n), replace=False).tolist())
    recs = read_records(sample, tof["file_idx"], tof["offset"], list(tof["files"]))

    ids = idm["chunk_id"].to_numpy()
    sha = idm["embed_sha1"].to_numpy()
    bad_id = sum(1 for r, rec in zip(sample, recs) if rec["chunk_id"] != ids[r])
    check("표본 chunk_id ↔ 원본 정합", bad_id == 0, f"불일치 {bad_id}건")

    texts = [rec["embedding_text"] for rec in recs]
    bad_sha = sum(
        1 for r, t in zip(sample, texts)
        if hashlib.sha1(t.encode()).hexdigest()[:16] != sha[r]
    )
    check("표본 embed_sha1 일치", bad_sha == 0, f"불일치 {bad_sha}건")

    import torch
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel(MODEL_NAME, use_fp16=True, normalize_embeddings=True, devices=args.device)
    q = np.asarray(
        model.encode(texts, batch_size=32, max_length=meta.get("max_length") or 1024,
                     return_dense=True, return_sparse=False, return_colbert_vecs=False)["dense_vecs"],
        dtype=np.float32,
    )
    dev = torch.device(args.device if torch.cuda.is_available() else "cpu")
    qt = torch.from_numpy(q).to(dev)

    best = torch.full((len(sample),), -1.0, device=dev)
    best_i = torch.zeros(len(sample), dtype=torch.long, device=dev)
    self_sim = torch.zeros(len(sample), device=dev)
    step = 50_000
    for i in range(0, n, step):
        blk = torch.from_numpy(np.asarray(dense[i : i + step], dtype=np.float32)).to(dev)
        sim = qt @ blk.T                       # (S, B)
        v, j = sim.max(dim=1)
        upd = v > best
        best_i = torch.where(upd, j + i, best_i)
        best = torch.where(upd, v, best)
        for k, r in enumerate(sample):         # 자기 자신과의 코사인
            if i <= r < i + blk.shape[0]:
                self_sim[k] = sim[k, r - i]
        del blk, sim

    top1 = best_i.cpu().numpy()
    self_cos = self_sim.cpu().numpy()
    exact = int((top1 == np.array(sample)).sum())
    # 동일 텍스트(같은 embed_sha1)가 1위면 중복이지 오류가 아니다
    dup = int(sum(1 for k, r in enumerate(sample) if top1[k] != r and sha[top1[k]] == sha[r]))
    hit = (exact + dup) / len(sample)
    check("셀프 리트리벌 top-1 ≥ 0.95", hit >= 0.95,
          f"{hit:.4f} (정확 {exact}/{len(sample)}, 동일텍스트 {dup}건)")
    check("자기 코사인 > 0.999", bool((self_cos > 0.999).all()),
          f"min={self_cos.min():.6f} mean={self_cos.mean():.6f}")

    miss = [(sample[k], int(top1[k])) for k in range(len(sample))
            if top1[k] != sample[k] and sha[top1[k]] != sha[sample[k]]]

    n_fail = sum(1 for _, ok, _ in CHECKS if not ok)
    lines = [
        "# 임베딩 건강검진", "",
        f"- 실행 시각: {datetime.now(timezone.utc).isoformat()}",
        f"- 모델: `{meta['model']}` / dim={meta['dim']} / max_length={meta.get('max_length')}",
        f"- 인덱스 행 수: {n:,} · 표본 {len(sample)}건 (seed={args.seed})",
        f"- 결과: **{'전 항목 통과' if n_fail == 0 else f'{n_fail}개 항목 실패'}**", "",
        "## 검사 항목", "", "| 항목 | 결과 | 상세 |", "| --- | --- | --- |",
    ]
    lines += [f"| {nm} | {'PASS' if ok else 'FAIL'} | {d} |" for nm, ok, d in CHECKS]
    lines += ["", "## 중단 조건 판정", "",
              "| 조건 | 판정 |", "| --- | --- |",
              f"| chunks / id_map / dense 행 수 불일치 | {'해당 없음' if ok_rows else '해당 — 중단'} |",
              f"| 셀프 리트리벌 top-1 < 0.95 | {'해당 없음' if hit >= 0.95 else '해당 — 중단'} |",
              f"| corp_code · doc_group 결측 | {'해당 없음' if all(ok for nm, ok, _ in CHECKS if '결측' in nm) else '해당 — 중단'} |"]
    if miss:
        lines += ["", "## 셀프 리트리벌 실패 행", "", "| row | top-1 row |", "| --- | --- |"]
        lines += [f"| {a} | {b} |" for a, b in miss[:20]]
    lines += ["", f"- 검사 소요: {time.time() - t0:.1f}초", ""]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[health] {REPORT.relative_to(ROOT)} 기록 — 실패 {n_fail}건", flush=True)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

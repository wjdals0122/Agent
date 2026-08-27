"""샤드 하나를 bge-m3로 임베딩하는 워커. GPU 1장 = 프로세스 1개.

  CUDA_VISIBLE_DEVICES=0 python -m src.index.embed_worker --rank 0 --world 4 \
      --max-length 1024 --batch-tokens 16384

torch.distributed도 FlagEmbedding의 devices=도 쓰지 않는다. 워커는 자기 샤드만
책임지고, 죽어도 다른 워커에 영향이 없다. 재실행하면 progress.{rank}.json을 보고
끝난 구간을 건너뛴다.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import pickle
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pyarrow.parquet as pq
from numpy.lib.format import open_memmap

# Windows에서 개발자 모드가 아니면 HF 캐시의 symlink 생성이 WinError 1314로 죽는다.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# 길이 정렬 배칭은 배치마다 시퀀스 길이가 달라 캐싱 할당자가 조각난다. 조각이 쌓이면
# 실제 할당량이 6GB인데도 예약량이 24GB까지 불어나 GPU를 가득 채운다.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from src.index import paths


# ---------- progress: 완료된 전역 row를 [start, end] 폐구간 리스트로 압축 보관 ----------

def compress(sorted_rows: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for r in sorted_rows:
        if out and r == out[-1][1] + 1:
            out[-1][1] = r
        else:
            out.append([r, r])
    return out


def expand(intervals) -> set[int]:
    done: set[int] = set()
    for a, b in intervals:
        done.update(range(a, b + 1))
    return done


class Progress:
    def __init__(self, path, rank: int):
        self.path, self.rank = path, rank
        self.done: set[int] = set()
        self.skipped: set[int] = set()
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                self.done = expand(d.get("done_rows", []))
                self.skipped = set(d.get("skipped_rows", []))
            except (json.JSONDecodeError, OSError) as e:
                print(f"[w{rank}] progress 손상, 처음부터 시작 — {e}", file=sys.stderr)

    def save(self, sparse_bytes: int) -> None:
        payload = {
            "rank": self.rank,
            "done_rows": compress(sorted(self.done)),
            "skipped_rows": sorted(self.skipped),
            "n_done": len(self.done),
            "sparse_bytes": sparse_bytes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        # Windows에서는 방금 쓴 파일을 바이러스 검사가 잠시 물고 있어 rename이 WinError 5로 튄다.
        for attempt in range(10):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        # 끝내 안 되면 원자성을 포기하고 직접 쓴다. 체크포인트 때문에 본 작업이 죽으면 안 된다.
        self.path.write_text(json.dumps(payload), encoding="utf-8")


# ---------- sparse: 배치마다 (row, {token_id: weight}) 리스트를 pickle 스트림으로 append ----------

def truncate_sparse_to_last_good(path) -> int:
    """중단된 실행이 남긴 꼬리 잘린 레코드를 잘라내고 유효 바이트 길이를 돌려준다."""
    if not path.exists():
        return 0
    good = 0
    with open(path, "rb") as fh:
        while True:
            try:
                pickle.load(fh)
            except (EOFError, pickle.UnpicklingError, AttributeError, ValueError):
                break
            good = fh.tell()
    if good != path.stat().st_size:
        with open(path, "r+b") as fh:
            fh.truncate(good)
        print(f"[sparse] 불완전한 꼬리 제거 → {good} bytes", flush=True)
    return good


# ---------- 토큰 예산 배칭 ----------

def make_batches(items: list[tuple[int, int]], batch_tokens: int, max_length: int, bytes_per_token: float):
    """items: n_bytes 오름차순 (local_idx, n_bytes). 패딩 포함 토큰 수가 예산을 넘지 않게 자른다."""
    batches, cur, cur_max = [], [], 0
    for local_idx, nb in items:
        est = min(max_length, max(1, int(nb / bytes_per_token) + 2))
        new_max = max(cur_max, est)
        if cur and new_max * (len(cur) + 1) > batch_tokens:
            batches.append(cur)
            cur, cur_max = [local_idx], est
        else:
            cur.append(local_idx)
            cur_max = new_max
    if cur:
        batches.append(cur)
    return batches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--world", type=int, default=4)
    ap.add_argument("--max-length", type=int, default=1024)
    ap.add_argument("--batch-tokens", type=int, default=16384)
    ap.add_argument("--limit", type=int, default=0, help="스모크 테스트: 샤드 앞 N행만")
    ap.add_argument("--bytes-per-token", type=float, default=3.0, help="배치 예산용 추정치(실측 중앙값 2.98)")
    ap.add_argument("--checkpoint-seconds", type=float, default=30.0, help="체크포인트 최소 간격")
    ap.add_argument(
        "--mem-fraction",
        type=float,
        default=0.0,
        help="이 프로세스가 쓸 GPU 메모리 상한 비율. 디스플레이 출력이 물려 있는 GPU에서는 "
        "0.8 정도로 잡아 데스크톱 몫을 남겨야 한다. 다 채우면 H2D 복사에서 멈춘다.",
    )
    args = ap.parse_args()

    rank, world = args.rank, args.world
    torch_threads = int(os.environ.get("OMP_NUM_THREADS", "8"))

    tbl = pq.read_table(paths.EMBED_TEXTS, columns=["row", "chunk_id", "embed_text", "n_bytes"])
    n_total = tbl.num_rows
    start = rank * n_total // world
    end = (rank + 1) * n_total // world
    if args.limit:
        end = min(end, start + args.limit)
    n_shard = end - start

    texts = tbl.column("embed_text").to_pylist()[start:end]
    nbytes = tbl.column("n_bytes").to_pylist()[start:end]
    del tbl
    gc.collect()

    print(
        f"[w{rank}] 샤드 rows [{start:,}, {end:,}) = {n_shard:,}건 | "
        f"max_length={args.max_length} batch_tokens={args.batch_tokens} threads={torch_threads}",
        flush=True,
    )

    dense_path = paths.WORK / f"dense.shard{rank}.npy"
    sparse_path = paths.WORK / f"sparse.shard{rank}.pkl"
    prog = Progress(paths.WORK / f"progress.{rank}.json", rank)

    mode = "r+" if dense_path.exists() else "w+"
    if mode == "r+":
        dense = open_memmap(dense_path, mode="r+")
        if dense.shape != (n_shard, paths.DIM):
            print(
                f"[w{rank}] 기존 샤드 shape {dense.shape} != {(n_shard, paths.DIM)} — "
                "샤드 경계가 바뀌었다. _work의 shard 파일을 지우고 다시 실행할 것.",
                file=sys.stderr,
            )
            return 2
    else:
        dense = open_memmap(dense_path, mode="w+", dtype=np.float32, shape=(n_shard, paths.DIM))
        prog.done.clear()
        prog.skipped.clear()

    sparse_bytes = truncate_sparse_to_last_good(sparse_path)
    # progress가 가리키는 것보다 sparse가 짧을 수는 없지만, 잘렸다면 done을 신뢰할 수 없다.
    # 그런 경우 done에서 sparse에 실제로 있는 row만 남긴다.
    if prog.done:
        have = set()
        with open(sparse_path, "rb") as fh:
            while True:
                try:
                    for r, _ in pickle.load(fh):
                        have.add(r)
                except (EOFError, pickle.UnpicklingError, ValueError):
                    break
        dropped = len(prog.done - have - prog.skipped)
        if dropped:
            print(f"[w{rank}] sparse에 없는 done row {dropped}건 재처리 대상으로 되돌림", flush=True)
        prog.done &= have | prog.skipped

    todo = [i for i in range(n_shard) if (start + i) not in prog.done and (start + i) not in prog.skipped]
    if not todo:
        print(f"[w{rank}] 이미 완료됨 — 처리할 행 없음", flush=True)
        prog.save(sparse_bytes)
        return 0
    print(f"[w{rank}] 처리 대상 {len(todo):,}건 (완료 {len(prog.done):,} / 스킵 {len(prog.skipped):,})", flush=True)

    # 길이 정렬: 패딩 낭비를 줄인다. 결과는 반드시 원래 인덱스에 쓴다.
    todo.sort(key=lambda i: nbytes[i])
    batches = make_batches([(i, nbytes[i]) for i in todo], args.batch_tokens, args.max_length, args.bytes_per_token)
    print(f"[w{rank}] 배치 {len(batches):,}개 (평균 {len(todo)/len(batches):.1f}건)", flush=True)

    import torch
    from FlagEmbedding import BGEM3FlagModel

    torch.set_num_threads(torch_threads)
    if args.mem_fraction:
        torch.cuda.set_per_process_memory_fraction(args.mem_fraction, 0)
        print(f"[w{rank}] GPU 메모리 상한 {args.mem_fraction:.0%}", flush=True)
    t_load = time.time()
    model = BGEM3FlagModel(paths.MODEL_NAME, use_fp16=True, normalize_embeddings=True, devices="cuda:0")
    load_seconds = time.time() - t_load
    print(f"[w{rank}] 모델 로드 {load_seconds:.1f}s", flush=True)

    sparse_fh = open(sparse_path, "ab")

    def encode(idxs: list[int]):
        out = model.encode(
            [texts[i] for i in idxs],
            batch_size=len(idxs),
            max_length=args.max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,  # 30만+ 청크면 수백 GB. 절대 켜지 않는다.
        )
        return out["dense_vecs"], out["lexical_weights"]

    def run(idxs: list[int], depth: int = 0) -> int:
        """성공한 건수를 돌려준다. OOM이면 반씩 쪼개 재시도, 끝내 실패하면 스킵."""
        try:
            vecs, lex = encode(idxs)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            if len(idxs) == 1:
                prog.skipped.add(start + idxs[0])
                print(f"[w{rank}] OOM 단건 스킵 row={start + idxs[0]} n_bytes={nbytes[idxs[0]]}", file=sys.stderr, flush=True)
                return 0
            mid = len(idxs) // 2
            print(f"[w{rank}] OOM — 배치 {len(idxs)} → {mid}/{len(idxs) - mid} 분할 재시도 (depth {depth})", flush=True)
            return run(idxs[:mid], depth + 1) + run(idxs[mid:], depth + 1)

        recs = []
        for k, i in enumerate(idxs):
            dense[i] = vecs[k].astype(np.float32, copy=False)
            recs.append((start + i, {int(t): float(w) for t, w in lex[k].items()}))
            prog.done.add(start + i)
        pickle.dump(recs, sparse_fh, protocol=pickle.HIGHEST_PROTOCOL)
        return len(idxs)

    t0 = time.time()
    n_ok = 0
    peak_gb = 0.0
    last_ckpt = 0.0
    for bi, idxs in enumerate(batches, 1):
        n_ok += run(idxs)
        # 체크포인트는 시간 간격으로. done 집합이 15만 건까지 자라므로 배치마다
        # compress(sorted(done))를 돌리면 그 비용이 임베딩보다 커진다.
        # 중간에 죽어도 progress에 안 적힌 배치만 다시 하면 되고, 재처리는 멱등이다.
        if time.time() - last_ckpt > args.checkpoint_seconds or bi == len(batches):
            dense.flush()
            sparse_fh.flush()
            os.fsync(sparse_fh.fileno())
            prog.save(sparse_fh.tell())
            last_ckpt = time.time()
        peak_gb = max(peak_gb, torch.cuda.max_memory_allocated() / 1e9)
        if bi % 20 == 0 or bi == len(batches):
            el = time.time() - t0
            rate = n_ok / el if el else 0
            eta = (len(todo) - n_ok) / rate if rate else 0
            print(
                f"[w{rank}] {bi}/{len(batches)} 배치 | {n_ok:,}/{len(todo):,}건 | "
                f"{rate:.1f} chunk/s | 경과 {el/60:.1f}분 | ETA {eta/60:.1f}분 | GPU peak {peak_gb:.1f}GB",
                flush=True,
            )

    sparse_fh.close()
    dense.flush()
    prog.save(sparse_path.stat().st_size)
    el = time.time() - t0

    stats = {
        "rank": rank,
        "rows": [start, end],
        "n_shard": n_shard,
        "n_encoded": n_ok,
        "n_skipped": len(prog.skipped),
        "seconds": round(el, 1),
        "chunks_per_sec": round(n_ok / el, 2) if el else None,
        "gpu_peak_gb": round(peak_gb, 2),
        "max_length": args.max_length,
        "batch_tokens": args.batch_tokens,
        "n_batches": len(batches),
        "model_load_seconds": round(load_seconds, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (paths.WORK / f"stats.{rank}.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[w{rank}] 완료 — {n_ok:,}건 / {el/60:.1f}분 / {n_ok/el if el else 0:.1f} chunk/s / "
        f"GPU peak {peak_gb:.1f}GB / 스킵 {len(prog.skipped)}건",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

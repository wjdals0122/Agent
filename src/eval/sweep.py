"""설정을 바꿔가며 검색 품질을 비교한다. 질의 인코딩은 문항당 한 번만 한다.

sparse 가중치와 하드필터가 실제로 순위를 개선하는지 보는 도구다.
가중치만 바꾸는 비교는 dense/sparse 점수를 재사용하므로 거의 공짜다.

  python -m src.eval.sweep                                   # 기본 그리드
  python -m src.eval.sweep --w-dense 1.0 0.0 --w-sparse 0 1 2 5
  python -m src.eval.sweep --k 1 3 5 10 --filter-mode both
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from src.eval import gpu_guard, metrics
from src.eval.run_eval import load_questions

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sweep(ret, questions, wd_list, w_list, k_list, filter_mode):
    """문항별로 점수 성분을 한 번 계산하고, 설정 조합마다 재랭킹만 한다."""
    settings = []
    for company_filter in ({"none", "hint"} if filter_mode == "both" else {filter_mode}):
        for wd in wd_list:
            for w in w_list:
                if wd == 0 and w == 0:
                    continue
                for k in k_list:
                    settings.append({"w_dense": wd, "w_sparse": w, "k": k, "filter": company_filter})
    settings.sort(key=lambda s: (s["filter"], s["w_dense"], s["w_sparse"], s["k"]))

    results = {json.dumps(s, sort_keys=True): [] for s in settings}
    max_k = max(k_list)

    for i, q in enumerate(questions, 1):
        t = time.time()
        dense_s, sparse_s, encode_ms = ret.score_components(q["question"])
        for s in settings:
            company = q.get("company") if s["filter"] == "hint" else None
            try:
                top, scores, _ = ret.rank(
                    dense_s, sparse_s, k=max_k, w_dense=s["w_dense"],
                    w_sparse=s["w_sparse"], company=company,
                )
            except ValueError:
                continue
            hits = ret.hydrate(top[: s["k"]], scores, dense_s, sparse_s, snippet=1)
            results[json.dumps(s, sort_keys=True)].append(metrics.score_hits(hits, q))
        print(f"[{i}/{len(questions)}] {q['id']} ({encode_ms:.0f}ms 인코딩, "
              f"{time.time() - t:.1f}초 x {len(settings)}설정)", flush=True)

    return [
        {**json.loads(key), **metrics.aggregate(rows), "n_questions": len(rows)}
        for key, rows in results.items()
        if rows
    ]


def to_markdown(rows, questions, meta) -> str:
    L = [
        "# 검색 설정 스윕",
        "",
        f"- 실행: {meta['built_at']}",
        f"- 질문 {len(questions)}개 · 인덱스 {meta['n_rows']:,}행 · GPU {meta['gpu']}",
        f"- GPU 온도: {meta['temp_summary']} · 총 {meta['elapsed_s']:.0f}초",
        "",
        "`section_*`은 기대 섹션 키워드가 결과의 섹션 경로/문서 제목에 걸렸는지다. 약한 기준이라",
        "절대값보다 설정 간 **상대 변화**를 본다. `strict@1`은 1위가 회사도 섹션도 맞은 비율로,",
        "실제로 답을 만들 수 있는 상태에 가장 가깝다.",
        "",
        "| 필터 | w_dense | w_sparse | k | section@1 | section@k | section MRR | company@1 | company@k | strict@1 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def fmt(v):
        return "—" if v is None else f"{v:.3f}"

    for r in sorted(rows, key=lambda r: (r["filter"], r["w_dense"], r["w_sparse"], r["k"])):
        L.append(
            f"| {r['filter']} | {r['w_dense']} | {r['w_sparse']} | {r['k']} | {fmt(r['section_at1'])} | "
            f"{fmt(r['section_atk'])} | {fmt(r['section_mrr'])} | {fmt(r['company_at1'])} | "
            f"{fmt(r['company_atk'])} | {fmt(r['strict_at1'])} |"
        )
    return "\n".join(L) + "\n"


def main() -> int:
    from src.index import paths

    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(paths.ROOT / "src" / "eval" / "questions.jsonl"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--w-dense", type=float, nargs="+", default=[1.0])
    ap.add_argument("--w-sparse", type=float, nargs="+", default=[0.0, 0.5, 1.0, 2.0, 3.0])
    ap.add_argument("--k", type=int, nargs="+", default=[1, 5, 10])
    ap.add_argument("--filter-mode", choices=("none", "hint", "both"), default="both")
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    ap.add_argument("--max-temp", type=float, default=80.0)
    ap.add_argument("--out", default="retrieval_sweep")
    args = ap.parse_args()

    if args.device == "cuda":
        gpu_guard.select_gpu(args.gpu)

    questions = load_questions(args.questions, args.limit, None)
    n_set = (len(args.w_dense) * len(args.w_sparse) * len(args.k)
             * (2 if args.filter_mode == "both" else 1))
    print(gpu_guard.format_table(gpu_guard.read_gpus()))
    print(f"\n질문 {len(questions)}개 x 설정 {n_set}개\n")

    from src.eval.retriever import Retriever

    t0 = time.time()
    with gpu_guard.TempMonitor(args.gpu) as mon:
        ret = Retriever(gpu=args.gpu, device=args.device, max_temp=args.max_temp)
        rows = sweep(ret, questions, args.w_dense, args.w_sparse, args.k, args.filter_mode)

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": ret.n,
        "gpu": args.gpu,
        "elapsed_s": round(time.time() - t0, 1),
        "temp_summary": mon.summary(),
    }

    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    md = paths.REPORTS / f"{args.out}.md"
    md.write_text(to_markdown(rows, questions, meta), encoding="utf-8")
    (paths.REPORTS / f"{args.out}.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + to_markdown(rows, questions, meta))
    print(f"[sweep] {md.relative_to(paths.ROOT)} 기록 · GPU {args.gpu} {meta['temp_summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

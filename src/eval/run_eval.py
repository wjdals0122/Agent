"""질문 세트를 한 번에 돌려 검색 품질을 눈으로 볼 수 있는 리포트로 떨군다.

  python -m src.eval.run_eval                          # questions.jsonl 전체
  python -m src.eval.run_eval --limit 5 -k 3           # 짧게 확인
  python -m src.eval.run_eval --filter-mode none       # 하드필터 없이 임베딩 실력만

채점 기준은 src/eval/metrics.py 참고. 청크 단위 정답 라벨이 없어 약한 기준을 쓰므로,
절대값보다 설정 간 상대 변화를 본다. 각 근거 앞의 O/X는 기대 섹션 키워드가 걸렸는지다.
최종 판단은 리포트의 근거 본문을 사람이 읽고 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from src.eval import gpu_guard, metrics

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUESTIONS = None  # main에서 채운다


def load_questions(path, limit=None, only=None):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if only:
        rows = [r for r in rows if r["id"] in only]
    return rows[:limit] if limit else rows


def run(ret, questions, k, w_dense, w_sparse, filter_mode, snippet):
    records = []
    for i, q in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] {q['id']} {q['question']}", flush=True)
        rec = {**q, "runs": {}}

        # 비교 질의는 단일 검색과 개체 분할을 나란히 돌려 차이를 남긴다
        if len(q.get("expect_companies") or []) >= 2:
            modes = [
                ("단일", lambda: ret.search(q["question"], k=k, w_dense=w_dense,
                                            w_sparse=w_sparse, snippet=snippet)),
                ("분할", lambda: ret.search_multi(q["question"], k=k, w_dense=w_dense,
                                                  w_sparse=w_sparse, snippet=snippet)),
            ]
        else:
            modes = []
            if filter_mode in ("none", "both"):
                modes.append(("필터없음", lambda: ret.search(
                    q["question"], k=k, w_dense=w_dense, w_sparse=w_sparse, snippet=snippet)))
            if filter_mode in ("hint", "both") and q.get("company"):
                modes.append(("회사필터", lambda: ret.search(
                    q["question"], k=k, w_dense=w_dense, w_sparse=w_sparse,
                    company=q["company"], snippet=snippet)))
            if not modes:
                modes = [("필터없음", lambda: ret.search(
                    q["question"], k=k, w_dense=w_dense, w_sparse=w_sparse, snippet=snippet))]

        for label, call in modes:
            t = time.time()
            try:
                res = call()
            except ValueError as e:
                print(f"    {label}: {e}", flush=True)
                rec["runs"][label] = {"error": str(e)}
                continue

            m = metrics.score_hits(res.hits, q)
            rec["runs"][label] = {
                **m,
                "n_candidates": res.n_candidates,
                "encode_ms": round(res.encode_ms, 1),
                "search_ms": round(res.search_ms, 1),
                "total_s": round(time.time() - t, 2),
                "top1": round(res.hits[0].score, 4) if res.hits else None,
                "score_gap": round(res.hits[0].score - res.hits[-1].score, 4) if len(res.hits) > 1 else 0.0,
                "n_unique_docs": len({h.chunk_id.split(":")[0] for h in res.hits}),
                "hits": [
                    {
                        "rank": h.rank, "row": h.row,
                        "score": round(h.score, 4), "dense": round(h.dense, 4), "sparse": round(h.sparse, 4),
                        "company": h.company, "corp_code": h.corp_code, "doc_group": h.doc_group,
                        "receipt_no": h.receipt_no, "document_title": h.document_title,
                        "section_path": h.section_path, "content": h.content,
                        "section_ok": metrics.section_ok(h, q),
                        "company_ok": metrics.company_ok(h, q),
                    }
                    for h in res.hits
                ],
            }
            ct = "n/a" if m["company_atk"] is None else f"{m['company_atk']:.0%}"
            ec = "" if m["entity_coverage"] is None else f"개체커버={m['entity_coverage']:.0%} "
            print(f"    {label}: top1={res.hits[0].score:.4f} 회사적중={ct} {ec}"
                  f"섹션@1={m['section_at1']:.0f} MRR={m['section_mrr']:.2f} "
                  f"({res.encode_ms:.0f}+{res.search_ms:.0f}ms)", flush=True)
        records.append(rec)
    return records


def to_markdown(records, meta) -> str:
    L = [
        "# 검색 스모크 리포트",
        "",
        f"- 실행: {meta['built_at']}",
        f"- 질문 {meta['n_questions']}개 · top-{meta['k']} · 가중치 dense {meta['w_dense']} / sparse {meta['w_sparse']}",
        f"- 인덱스 {meta['n_rows']:,}행 · 장치 {meta['device']} (GPU {meta['gpu']})",
        f"- GPU 온도: {meta['temp_summary']}",
        "",
        "정답 라벨이 없어 아래 수치는 대리 지표다. 근거 본문을 직접 읽고 판단할 것.",
        "",
        "## 요약",
        "",
        "| id | 질문 | 모드 | top1 | 회사@1 | 회사@k | 섹션@1 | 섹션@k | MRR | 문서수 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        for label, run in r["runs"].items():
            if "error" in run:
                L.append(f"| {r['id']} | {r['question'][:26]} | {label} | {run['error']} | | | | | | |")
                continue
            def pct(v):
                return "n/a" if v is None else f"{v:.0%}"
            L.append(
                f"| {r['id']} | {r['question'][:26]} | {label} | {run['top1']:.4f} | "
                f"{pct(run['company_at1'])} | {pct(run['company_atk'])} | "
                f"{pct(run['section_at1'])} | {pct(run['section_atk'])} | "
                f"{run['section_mrr']:.2f} | {run['n_unique_docs']} |"
            )

    no_filter = [r["runs"]["필터없음"] for r in records if "필터없음" in r["runs"]
                 and "error" not in r["runs"]["필터없음"]]
    if no_filter:
        agg = metrics.aggregate(no_filter)
        L += ["", "**필터 없이 임베딩만으로** (전체 61만 건 대상)", ""]
        L += [f"- 회사 적중 @1 **{agg['company_at1']:.0%}** / @k **{agg['company_atk']:.0%}** "
              f"({agg['company_at1_n']}문항)"]
        L += [f"- 섹션 적중 @1 **{agg['section_at1']:.0%}** / @k **{agg['section_atk']:.0%}** "
              f"· MRR **{agg['section_mrr']:.3f}** ({agg['section_at1_n']}문항)"]
        if agg["strict_at1"] is not None:
            L += [f"- 1위가 회사·섹션 모두 적중 **{agg['strict_at1']:.0%}** ({agg['strict_at1_n']}문항)"]

    for label, title in (("단일", "단일 검색"), ("분할", "개체 분할 검색")):
        runs = [r["runs"][label] for r in records
                if label in r["runs"] and "error" not in r["runs"][label]]
        if runs:
            a = metrics.aggregate(runs)
            L += ["", f"**비교 질의 — {title}**", "",
                  f"- 개체 커버리지 **{a['entity_coverage']:.0%}** ({a['entity_coverage_n']}문항)",
                  f"- 섹션 적중 @1 {a['section_at1']:.0%} · MRR {a['section_mrr']:.3f}"]

    L += ["", "## 질문별 근거", ""]
    for r in records:
        L += [f"### {r['id']} — {r['question']}", ""]
        if r.get("expect_any"):
            L.append(f"기대 섹션 키워드: {', '.join(r['expect_any'])}  ")
        if r.get("company"):
            L.append(f"기대 회사: {r['company']}  ")
        L.append(f"태그: {', '.join(r.get('tags', []))}")
        L.append("")
        for label, run in r["runs"].items():
            L += [f"**{label}** (후보 {run.get('n_candidates', 0):,}건)", ""]
            if "error" in run:
                L += [f"> {run['error']}", ""]
                continue
            for h in run["hits"]:
                L += [
                    f"{h['rank']}. {'O' if h['section_ok'] else 'X'} `{h['score']:.4f}` "
                    f"(d {h['dense']:.4f} / s {h['sparse']:.4f}) "
                    f"**{h['company']}** · {h['doc_group']} · {h['receipt_no']}  ",
                    f"   {h['document_title']}  ",
                    f"   § {h['section_path']}  ",
                    "",
                    "   > " + h["content"].strip().replace("\n", "\n   > "),
                    "",
                ]
    return "\n".join(L) + "\n"


def main() -> int:
    from src.index import paths

    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(paths.ROOT / "src" / "eval" / "questions.jsonl"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", nargs="*", help="질문 id만 골라서")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--w-dense", type=float, default=1.0)
    ap.add_argument("--w-sparse", type=float, default=1.0)
    ap.add_argument("--filter-mode", choices=("none", "hint", "both"), default="both")
    ap.add_argument("--dense-only", action="store_true")
    ap.add_argument("--snippet", type=int, default=350)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    ap.add_argument("--max-temp", type=float, default=80.0)
    ap.add_argument("--resume-temp", type=float, default=70.0)
    ap.add_argument("--out", default="retrieval_eval")
    args = ap.parse_args()

    if args.device == "cuda":
        if args.gpu == gpu_guard.DISPLAY_GPU:
            print(f"[경고] GPU {args.gpu}는 디스플레이 GPU다. --gpu 0~2를 권한다.")
        gpu_guard.select_gpu(args.gpu)

    questions = load_questions(args.questions, args.limit, args.only)
    print(gpu_guard.format_table(gpu_guard.read_gpus()))
    print(f"\n질문 {len(questions)}개 · top-{args.k} · 필터모드 {args.filter_mode}\n")

    from src.eval.retriever import Retriever

    t0 = time.time()
    with gpu_guard.TempMonitor(args.gpu) as mon:
        ret = Retriever(
            gpu=args.gpu, device=args.device, use_sparse=not args.dense_only,
            max_temp=args.max_temp, resume_temp=args.resume_temp,
        )
        records = run(ret, questions, args.k, args.w_dense, args.w_sparse,
                      args.filter_mode, args.snippet)

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_questions": len(questions),
        "k": args.k,
        "w_dense": args.w_dense,
        "w_sparse": args.w_sparse,
        "n_rows": ret.n,
        "device": args.device,
        "gpu": args.gpu,
        "filter_mode": args.filter_mode,
        "elapsed_s": round(time.time() - t0, 1),
        "temp_summary": mon.summary(),
    }

    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    md = paths.REPORTS / f"{args.out}.md"
    js = paths.REPORTS / f"{args.out}.json"
    md.write_text(to_markdown(records, meta), encoding="utf-8")
    js.write_text(json.dumps({"meta": meta, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[eval] {md.relative_to(paths.ROOT)} / {js.relative_to(paths.ROOT)} 기록")
    print(f"[eval] 총 {meta['elapsed_s']:.0f}초 · GPU {args.gpu} {meta['temp_summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

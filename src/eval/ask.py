"""질의 -> 검색 결과 확인용 CLI. 인덱스가 질문에 맞는 근거를 물어오는지 눈으로 본다.

  python -m src.eval.ask "삼성전자 2023년 배당 정책"
  python -m src.eval.ask "유상증자 목적" --company 카카오 --year 2024 -k 5
  python -m src.eval.ask                      # 대화형. 인덱스를 한 번만 올리고 계속 물어본다

대화형에서 쓰는 지시어:
  /company 삼성전자   /year 2023   /group periodic   /k 8   /w 1.0   /full   /reset   /gpu   /q
"""
from __future__ import annotations

import argparse
import sys

from src.eval import gpu_guard

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOC_GROUPS = ("periodic", "exchange", "major", "holding")


def render(result, snippet: int) -> str:
    f = result.filters
    active = [f"{k}={v}" for k, v in (("회사", f["company"]), ("연도", f["year"]), ("유형", f["doc_group"])) if v]
    head = [
        "",
        f"질의: {result.query}",
        f"후보 {result.n_candidates:,}건"
        + (f" · 필터 {' '.join(active)}" if active else " · 필터 없음")
        + f" · 인코딩 {result.encode_ms:.0f}ms · 검색 {result.search_ms:.0f}ms"
        + f" · 가중치 dense {f['w_dense']}/sparse {f['w_sparse']}",
    ]
    if len(result.entities) > 1:
        head.append(f"개체 분할: {' / '.join(result.entities)} — 회사별로 나눠 뽑았다")
    head += [
        "",
    ]
    for h in result.hits:
        head += [
            f"[{h.rank}] {h.score:.4f}  (dense {h.dense:.4f} · sparse {h.sparse:.4f})",
            f"    {h.company} ({h.corp_code}) · {h.doc_group} · 접수 {h.receipt_no} · row {h.row}",
            f"    {h.document_title}",
            f"    § {h.section_path}" if h.section_path else "    §",
            "",
        ]
        body = h.content.strip().replace("\n", "\n    ")
        head += [f"    {body}", ""]
    return "\n".join(head)


def run_query(ret, query, args, *, k, w_sparse, company, group, year, snippet):
    """회사 하드필터를 직접 걸었거나 --no-split이면 단일 검색, 아니면 개체 분할."""
    cap = args.max_per_doc or None
    if args.no_split or company:
        return ret.search(
            query, k=k, w_dense=args.w_dense, w_sparse=w_sparse,
            company=company, doc_group=group, year=year, snippet=snippet,
        )
    return ret.search_multi(
        query, k=k, w_dense=args.w_dense, w_sparse=w_sparse,
        doc_group=group, year=year, snippet=snippet, max_per_doc=cap,
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="비우면 대화형으로 들어간다")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--company", help="회사명 일부 또는 6자리 종목코드")
    ap.add_argument("--year", type=int, action="append", help="공시 접수연도. 여러 번 줄 수 있다")
    ap.add_argument("--group", choices=DOC_GROUPS, action="append")
    ap.add_argument("--w-dense", type=float, default=1.0)
    ap.add_argument("--w-sparse", type=float, default=1.0)
    ap.add_argument("--dense-only", action="store_true", help="sparse를 아예 안 올린다 (메모리 1GB 절약)")
    ap.add_argument("--snippet", type=int, default=400, help="0이면 청크 전문")
    ap.add_argument("--gpu", type=int, default=0, help="쓸 물리 GPU. 3은 디스플레이 GPU라 피할 것")
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    ap.add_argument("--max-temp", type=float, default=80.0)
    ap.add_argument("--resume-temp", type=float, default=70.0)
    ap.add_argument("--no-split", action="store_true",
                    help="회사가 여럿이어도 분할하지 않고 한 번에 검색한다 (비교 실패를 재현할 때)")
    ap.add_argument("--latest-only", action="store_true",
                    help="정정 재제출로 밀려난 옛 정기보고서를 후보에서 뺀다")
    ap.add_argument("--max-per-doc", type=int, default=1,
                    help="한 공시 문서에서 뽑을 최대 청크 수. 0이면 제한 없음")
    return ap


def repl(ret, args) -> None:
    state = {
        "company": args.company,
        "year": args.year,
        "group": args.group,
        "k": args.k,
        "w_sparse": args.w_sparse,
        "snippet": args.snippet,
    }
    print("\n질문을 입력하세요. 지시어는 /company /year /group /k /w /full /reset /gpu /q\n")
    while True:
        try:
            line = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue

        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            rest = rest.strip()
            if cmd in ("q", "quit", "exit"):
                return
            if cmd == "gpu":
                print(gpu_guard.format_table(gpu_guard.read_gpus()))
            elif cmd == "company":
                state["company"] = rest or None
                if rest:
                    print(f"  일치: {', '.join(ret.resolve_company(rest)) or '없음'}")
            elif cmd == "year":
                state["year"] = [int(x) for x in rest.split()] if rest else None
            elif cmd == "group":
                state["group"] = rest.split() if rest else None
            elif cmd == "k":
                state["k"] = int(rest)
            elif cmd == "w":
                state["w_sparse"] = float(rest)
            elif cmd == "full":
                state["snippet"] = 0 if state["snippet"] else 400
                print(f"  스니펫: {'전문' if state['snippet'] == 0 else '400자'}")
            elif cmd == "reset":
                state.update(company=None, year=None, group=None)
            else:
                print(f"  모르는 지시어: /{cmd}")
            print(f"  현재 필터 — 회사={state['company']} 연도={state['year']} 유형={state['group']} "
                  f"k={state['k']} w_sparse={state['w_sparse']}")
            continue

        try:
            res = run_query(
                ret, line, args, k=state["k"], w_sparse=state["w_sparse"],
                company=state["company"], group=state["group"], year=state["year"],
                snippet=state["snippet"],
            )
            print(render(res, state["snippet"]))
        except ValueError as e:
            print(f"  {e}")


def main() -> int:
    args = build_parser().parse_args()

    if args.device == "cuda":
        if args.gpu == gpu_guard.DISPLAY_GPU:
            print(f"[경고] GPU {args.gpu}는 디스플레이 GPU다. --gpu 0~2를 권한다.")
        gpu_guard.select_gpu(args.gpu)

    print(gpu_guard.format_table(gpu_guard.read_gpus()))

    from src.eval.retriever import Retriever

    with gpu_guard.TempMonitor(args.gpu) as mon:
        ret = Retriever(
            gpu=args.gpu,
            device=args.device,
            use_sparse=not args.dense_only,
            latest_only=args.latest_only,
            max_temp=args.max_temp,
            resume_temp=args.resume_temp,
        )
        if args.query:
            res = run_query(
                ret, args.query, args, k=args.k, w_sparse=args.w_sparse,
                company=args.company, group=args.group, year=args.year,
                snippet=args.snippet,
            )
            print(render(res, args.snippet))
        else:
            repl(ret, args)

    print(f"[guard] GPU {args.gpu} {mon.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

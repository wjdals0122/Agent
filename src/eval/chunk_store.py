"""row -> 원본 청크 레코드 조회.

벡터 인덱스에는 embed_sha1과 메타데이터만 있고 표시용 원문(`content`)이 없다.
원본 JSONL을 한 번 훑어 줄마다 바이트 오프셋을 적어두고, 검색 결과 상위 k건만
그 자리를 seek 해서 읽는다. 3GB를 통째로 메모리에 올리지 않으려는 것이다.

  python -m src.eval.chunk_store --build     # 오프셋 사이드카 생성 (1회)
  python -m src.eval.chunk_store --peek 42   # 42행 내용 확인
"""
from __future__ import annotations

import argparse
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import orjson
import pyarrow.parquet as pq

from src.index import paths

OFFSETS = paths.VECTORS / "text_offsets.npz"


def _mark_superseded(row_doc, doc_meta) -> np.ndarray:
    """정정 재제출로 밀려난 옛 정기보고서의 행을 표시한다.

    정기보고서는 제목에 기간이 들어 있어서(예: 사업보고서 (2023.12)) 같은 회사·같은 제목이면
    같은 보고서다. 접수번호가 여럿이면 정정 재제출이고 가장 큰 접수번호가 최신본이다.

    이벤트 공시(major/exchange/holding)에는 이 규칙을 쓰면 안 된다. 「단일판매ㆍ공급계약체결」
    처럼 제목이 같아도 매번 다른 사건이다 (삼성E&A는 이 제목으로 87건을 냈다).
    또 접수번호가 같은데 doc_id가 다른 것은 한 공시가 본문·첨부로 쪼개진 것이니 건드리지 않는다.
    """
    by_title = {}
    for doc, (comp, title, rcept, group) in doc_meta.items():
        if group == "periodic":
            by_title.setdefault((comp, title), set()).add(rcept)

    stale = {
        doc
        for doc, (comp, title, rcept, group) in doc_meta.items()
        if group == "periodic"
        and len(by_title.get((comp, title), ())) > 1
        and rcept != max(by_title[(comp, title)])
    }
    return np.fromiter((d in stale for d in row_doc), dtype=bool, count=len(row_doc))


def build(verify: bool = True) -> None:
    files = paths.chunk_files()
    file_idx: list[int] = []
    offsets: list[int] = []
    ids: list[str] = []
    row_doc: list[str] = []
    doc_meta: dict[str, tuple] = {}

    t0 = time.time()
    for fi, path in enumerate(files):
        pos = 0
        with open(path, "rb") as fh:
            for raw in fh:
                start, pos = pos, pos + len(raw)
                if not raw.strip():
                    continue
                file_idx.append(fi)
                offsets.append(start)
                rec = orjson.loads(raw)
                doc = rec["chunk_id"].split(":")[0]
                row_doc.append(doc)
                if doc not in doc_meta:
                    doc_meta[doc] = (
                        rec.get("company", ""), rec.get("document_title", ""),
                        rec.get("receipt_no", ""), rec.get("disclosure_type", ""),
                    )
                if verify:
                    ids.append(rec["chunk_id"])
        print(f"  {path.name}: 누적 {len(offsets):,}행", flush=True)

    superseded = _mark_superseded(row_doc, doc_meta)
    print(f"  정정 재제출로 밀려난 옛 정기보고서: {int(superseded.sum()):,}행 "
          f"({superseded.mean():.2%})", flush=True)

    if verify:
        # row 순서 계약이 깨지면 여기서 잡힌다. id_map의 row가 유일한 계약이다.
        expected = pq.read_table(paths.ID_MAP, columns=["chunk_id"]).column("chunk_id").to_pylist()
        if len(ids) != len(expected):
            raise SystemExit(f"행 수 불일치: JSONL {len(ids):,} vs id_map {len(expected):,}")
        bad = [i for i, (a, b) in enumerate(zip(ids, expected)) if a != b]
        if bad:
            raise SystemExit(f"chunk_id 순서 불일치 {len(bad):,}건, 첫 행 {bad[0]}")
        print(f"  chunk_id {len(ids):,}행 전량 일치", flush=True)

    np.savez(
        OFFSETS,
        file_idx=np.asarray(file_idx, dtype=np.int8),
        offset=np.asarray(offsets, dtype=np.int64),
        files=np.asarray([p.name for p in files]),
        superseded=superseded,
    )
    print(f"[chunk_store] {OFFSETS.name} 기록 — {len(offsets):,}행, {time.time()-t0:.1f}초")


class ChunkStore:
    """열린 파일 핸들을 들고 있다가 요청받은 행만 seek 해서 읽는다."""

    def __init__(self) -> None:
        if not OFFSETS.exists():
            raise FileNotFoundError(
                f"{OFFSETS} 가 없다. 먼저 python -m src.eval.chunk_store --build"
            )
        z = np.load(OFFSETS, allow_pickle=False)
        self.file_idx = z["file_idx"]
        self.offset = z["offset"]
        # 예전 사이드카에는 없다. 없으면 전부 최신본으로 본다.
        self.superseded = (
            z["superseded"] if "superseded" in z.files
            else np.zeros(len(self.offset), dtype=bool)
        )
        names = [str(x) for x in z["files"]]
        self._handles = [open(paths.CHUNKS_DIR / n, "rb") for n in names]

    def __len__(self) -> int:
        return len(self.offset)

    def get(self, row: int) -> dict:
        fh = self._handles[int(self.file_idx[row])]
        fh.seek(int(self.offset[row]))
        return orjson.loads(fh.readline())

    def close(self) -> None:
        for fh in self._handles:
            fh.close()

    def __enter__(self) -> "ChunkStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="빌드 시 chunk_id 대조 생략")
    ap.add_argument("--peek", type=int, help="해당 row 레코드 출력")
    args = ap.parse_args()

    if args.build:
        build(verify=not args.no_verify)
    if args.peek is not None:
        with ChunkStore() as st:
            rec = st.get(args.peek)
            for k, v in rec.items():
                s = str(v)
                print(f"{k:>16}: {s[:300]}{'...' if len(s) > 300 else ''}")
    if not args.build and args.peek is None:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

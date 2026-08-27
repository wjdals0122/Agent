"""청크 소스를 한 번만 읽어 _work/embed_texts.parquet + id_map.parquet + meta.json 초안을 만든다.

워커 4개가 각자 3.1GB JSONL을 다시 파싱하지 않게 하려는 단계다.
dense.f32.npy는 여기서 만들지 않는다 (embed_merge.py 담당).

  python -m src.index.embed_prepare [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone

import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from src.index import paths

# 지시서의 입력 계약 → 실제 JSONL 필드명. 승인된 매핑이다.
#   embed_text  → embedding_text
#   corp_code   → stock_code (종목코드)
#   doc_group   → disclosure_type
# base_year / rcept_dt는 소스에 없어 receipt_no(14자리 YYYYMMDD+6)에서 기계적으로 추출한다.
REQUIRED = ("chunk_id", "embedding_text", "doc_id", "stock_code", "disclosure_type", "receipt_no")


def sha1_16(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="스모크 테스트용. 앞 N행만 처리")
    args = ap.parse_args()

    paths.WORK.mkdir(parents=True, exist_ok=True)
    files = paths.chunk_files()
    print(f"[prepare] 소스 {len(files)}개 파일", flush=True)

    rows, chunk_ids, embed_texts, n_bytes = [], [], [], []
    doc_ids, corp_codes, doc_groups, base_years, rcept_dts = [], [], [], [], []
    companies, receipt_nos, embed_sha1s = [], [], []
    file_sha1: dict[str, str] = {}

    t0 = time.time()
    row = 0
    stop = False
    for path in files:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for lineno, raw in enumerate(fh, 1):
                h.update(raw)
                if stop:
                    continue  # 해시는 파일 전체로 계산해야 하므로 읽기는 계속한다
                if not raw.strip():
                    continue
                try:
                    rec = orjson.loads(raw)
                except orjson.JSONDecodeError as e:
                    print(f"[prepare] JSON 파싱 실패 {path.name}:{lineno} — {e}", file=sys.stderr)
                    return 2

                missing = [f for f in REQUIRED if f not in rec or rec[f] in (None, "")]
                if missing:
                    print(
                        f"[prepare] 필수 필드 누락 {path.name}:{lineno} chunk_id="
                        f"{rec.get('chunk_id')!r} 누락={missing}\n"
                        "[prepare] 입력 계약 위반이므로 중단한다. 임의로 채우지 않는다.",
                        file=sys.stderr,
                    )
                    return 2

                text = rec["embedding_text"]
                receipt = str(rec["receipt_no"])
                if len(receipt) < 8 or not receipt[:8].isdigit():
                    print(
                        f"[prepare] receipt_no 형식 이상 {path.name}:{lineno} — {receipt!r}",
                        file=sys.stderr,
                    )
                    return 2

                rows.append(row)
                chunk_ids.append(rec["chunk_id"])
                embed_texts.append(text)
                n_bytes.append(len(text.encode()))
                doc_ids.append(rec["doc_id"])
                corp_codes.append(str(rec["stock_code"]))
                doc_groups.append(rec["disclosure_type"])
                base_years.append(int(receipt[:4]))
                rcept_dts.append(receipt[:8])
                companies.append(rec.get("company") or rec.get("company_from_filename") or "")
                receipt_nos.append(receipt)
                embed_sha1s.append(sha1_16(text))
                row += 1

                if args.limit and row >= args.limit:
                    stop = True
        file_sha1[path.name] = h.hexdigest()
        print(f"[prepare] {path.name} 완료 — 누적 {row:,}행 ({time.time() - t0:.1f}s)", flush=True)

    n = row
    if n == 0:
        print("[prepare] 처리된 행이 0건이다.", file=sys.stderr)
        return 2

    dup = n - len(set(chunk_ids))
    if dup:
        print(f"[prepare] chunk_id 중복 {dup}건 — 고유 키 계약 위반이므로 중단한다.", file=sys.stderr)
        return 2

    pq.write_table(
        pa.table(
            {
                "row": pa.array(rows, pa.int64()),
                "chunk_id": pa.array(chunk_ids, pa.string()),
                "embed_text": pa.array(embed_texts, pa.string()),
                "n_bytes": pa.array(n_bytes, pa.int32()),
            }
        ),
        paths.EMBED_TEXTS,
        compression="zstd",
    )
    print(f"[prepare] {paths.EMBED_TEXTS.name} 기록 — {n:,}행", flush=True)

    pq.write_table(
        pa.table(
            {
                "row": pa.array(rows, pa.int64()),
                "chunk_id": pa.array(chunk_ids, pa.string()),
                "doc_id": pa.array(doc_ids, pa.string()),
                "corp_code": pa.array(corp_codes, pa.string()),
                "doc_group": pa.array(doc_groups, pa.string()),
                "base_year": pa.array(base_years, pa.int16()),
                "rcept_dt": pa.array(rcept_dts, pa.string()),
                "company": pa.array(companies, pa.string()),
                "receipt_no": pa.array(receipt_nos, pa.string()),
                "embed_sha1": pa.array(embed_sha1s, pa.string()),
            }
        ),
        paths.ID_MAP,
        compression="zstd",
    )
    print(f"[prepare] {paths.ID_MAP.name} 기록", flush=True)

    meta = {
        "model": paths.MODEL_NAME,
        "dim": paths.DIM,
        "normalized": True,
        "max_length": None,  # 워커 실행 시 기록
        "n_rows": n,
        "sparse_vocab": paths.SPARSE_VOCAB,
        "chunks_source": [str(p.relative_to(paths.ROOT)) for p in files],
        "chunks_file_sha1": file_sha1,
        "field_mapping": {
            "embed_text": "embedding_text",
            "corp_code": "stock_code",
            "doc_group": "disclosure_type",
            "base_year": "int(receipt_no[:4])",
            "rcept_dt": "receipt_no[:8]",
        },
        "limit": args.limit or None,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "prepare_seconds": round(time.time() - t0, 1),
    }
    paths.META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prepare] meta.json 기록 — n_rows={n:,}, {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

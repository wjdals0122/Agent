"""색인된 청크를 '정준 순서'로 다시 읽는 이터레이터.

행 순서 계약(CLAUDE.md 규칙 3): 청크 파일의 행 순서 = dense.f32.npy 행
= id_map.parquet 행 = BM25 행. 어디서도 정렬·재배치하지 않는다.

정준 순서의 근거는 glob 결과나 파일명 정렬이 아니라
data/index/vectors/meta.json 의 chunks_source 배열이다.
임베딩 당시 실제로 사용된 파일 순서가 거기에 기록돼 있고, 그것만이 계약이다.
(meta 의 경로는 임베딩 당시의 chunks_v2/ 를 가리키므로 파일명만 쓰고
 위치는 src/index/paths.CHUNKS_DIR 로 해석한다.)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

import orjson

from src.index import paths


def canonical_files() -> list[Path]:
    """meta.json 이 기록한 순서 그대로의 청크 파일 목록."""
    meta = json.loads(paths.META.read_text(encoding="utf-8"))
    src = meta.get("chunks_source")
    if not src:
        raise RuntimeError(f"{paths.META} 에 chunks_source 가 없다 — 정준 순서를 복원할 수 없다")
    files = [paths.CHUNKS_DIR / Path(p).name for p in src]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise FileNotFoundError("정준 목록의 청크 파일이 없음: " + ", ".join(missing))
    return files


def skipped_doc_ids() -> set[str]:
    """임베딩 단계가 건너뛴 doc_id. meta 우선, 없으면 청커 manifest."""
    meta = json.loads(paths.META.read_text(encoding="utf-8"))
    return set(meta.get("skipped_doc_ids") or ()) or paths.skipped_doc_ids()


def iter_chunks_canonical() -> Iterator[dict]:
    """색인 row 0..N-1 과 1:1 로 대응하는 청크 dict 를 순서대로 내놓는다."""
    skip = skipped_doc_ids()
    for path in canonical_files():
        with open(path, "rb") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                rec = orjson.loads(raw)
                if rec.get("doc_id") in skip:
                    continue
                yield rec


def embed_sha1(text: str) -> str:
    """id_map.embed_sha1 과 같은 규칙 (sha1 앞 16자)."""
    return hashlib.sha1(text.encode()).hexdigest()[:16]

"""공통 경로·상수. 임베딩 파이프라인 전 단계가 여기를 본다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 입력: 지시서는 data/processed/chunks.jsonl.gz 하나를 가정했으나, 실제 배포본은
# 회사 10개 단위로 쪼개진 .jsonl 7개다. 파일명 정렬 순서가 곧 row 순서 계약이다.
#
# 이 레포의 06_build_chunks.py 도 data/processed/chunks.jsonl.gz 를 만든다.
# 둘은 **다른 코퍼스**다 — 아래 벡터는 배포본(70개사 614,693청크)으로 만든 것이고,
# 레포 자체 청킹 결과로 다시 임베딩하려면 DART_CHUNKS_DIR 로 갈아끼운다.
CHUNKS_DIR = Path(
    os.environ.get(
        "DART_CHUNKS_DIR",
        ROOT / "data" / "processed" / "chunks_by_10_companies",
    )
)
# companies_* 7개 + pdf_docs 1개. 정렬하면 pdf_docs 가 맨 뒤에 붙는다(c < p).
CHUNKS_GLOB = "disclosure_chunks_*.jsonl"

# 청커가 남긴 목록. skipped_doc_ids 는 마커 붙은 새 버전으로 대체된 옛 문서다.
# 파일에는 남아 있으므로 임베딩 단계에서 걸러야 한다.
CHUNKS_MANIFEST = "manifest.json"

# 출력도 갈아끼울 수 있어야 한다. 청크를 바꿔 재임베딩할 때 기존 벡터를 덮으면
# 40문항 실측 기준선과 비교할 대상이 사라진다.
#   DART_VECTORS_DIR=data/index/vectors_v2 python -m src.index.embed_prepare
VECTORS = Path(
    os.environ.get("DART_VECTORS_DIR", ROOT / "data" / "index" / "vectors")
)
WORK = VECTORS / "_work"
# 검증 리포트는 레포 루트 reports/ 로 모은다(scripts/pipeline_paths.py 와 같은 규칙).
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"

DENSE = VECTORS / "dense.f32.npy"
SPARSE = VECTORS / "sparse.npz"
ID_MAP = VECTORS / "id_map.parquet"
META = VECTORS / "meta.json"
EMBED_TEXTS = WORK / "embed_texts.parquet"

MODEL_NAME = "BAAI/bge-m3"
DIM = 1024
SPARSE_VOCAB = 250002


def chunk_files() -> list[Path]:
    files = sorted(CHUNKS_DIR.glob(CHUNKS_GLOB))
    if not files:
        raise FileNotFoundError(f"청크 파일을 찾을 수 없음: {CHUNKS_DIR / CHUNKS_GLOB}")
    return files


def skipped_doc_ids() -> set[str]:
    """청커 manifest 가 '대체됐다'고 표시한 doc_id.

    정정 위치를 마킹한 새 버전이 들어오면서 밀려난 옛 문서다. 청크 파일에는
    그대로 남아 있으므로 여기서 걸러내지 않으면 같은 보고서가 두 벌 색인된다.
    manifest 가 없으면 빈 집합 — 스킵 없이 전량 임베딩한다.
    """
    import json

    path = CHUNKS_DIR / CHUNKS_MANIFEST
    if not path.is_file():
        return set()
    man = json.loads(path.read_text(encoding="utf-8"))
    return set(man.get("skipped_doc_ids") or ())

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
CHUNKS_GLOB = "disclosure_chunks_companies_*.jsonl"

VECTORS = ROOT / "data" / "index" / "vectors"
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

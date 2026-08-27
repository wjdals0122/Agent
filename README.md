# dis-044 — 공시 RAG 파이프라인

AI Festival 2026 · 팀인천

DART 원문(XML/PDF) → **파싱·정규화 → 청킹 → bge-m3 임베딩 → 검색 평가**까지 한 레포에서 재현한다.

---

## Quick Start

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
# torch는 CUDA 버전에 맞춰 별도 설치 (requirements.txt 주석 참고)

cp scripts/config.example.json scripts/config.json   # 경로·옵션

# 1) 파싱 → 청킹
python scripts/00_freeze_baseline.py       # 회귀 기준선 동결
python scripts/03_build_docjson.py         # XML → doc.json
python scripts/05_build_doc_index.py       # 문서 메타 인덱스
python scripts/05b_build_md_index.py       # md 파일 메타 인덱스
python scripts/06_build_chunks.py --jobs 10   # → data/processed/chunks.jsonl.gz
python scripts/07_coverage.py              # 본문 손실 검증
python scripts/99_validate.py --all        # 검증 골든셋

# 2) 임베딩 (GPU 4장)
python -m src.index.embed_prepare
bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384
python -m src.index.embed_merge && python -m src.index.verify_vectors

# 3) 검색 확인
python -m src.eval.chunk_store --build     # 1회
python -m src.eval.ask                     # 대화형
```

---

## 주요 명령어

| 용도 | 명령어 |
|------|--------|
| 파싱 → doc.json | `python scripts/03_build_docjson.py` |
| md 파일 메타 인덱스 | `python scripts/05b_build_md_index.py` |
| 청킹 | `python scripts/06_build_chunks.py --jobs 10` |
| 손실·환각 검증 | `python scripts/07_coverage.py` / `python scripts/99_validate.py --all` |
| 임베딩 (4 GPU) | `bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384` |
| 인덱스 검증 | `python -m src.index.verify_vectors` |
| 단건 질의 | `python -m src.eval.ask "삼성전자의 주주환원 정책은?"` |
| 질문 세트 일괄 | `python -m src.eval.run_eval -k 5` |
| 설정 그리드 비교 | `python -m src.eval.sweep` |

---

## 구조

- **corpus/** — DART 원문(읽기 전용, 미추적). `manifest.jsonl`이 문서 목록
- **baseline/** — 0단계 회귀 기준선의 **해시**. 기준선이 이 기계에만 있으면 기준선이 아니다
- **config/** — `exception_policy.yaml` (무엇을 예외로 잡을지)
- **scripts/** — `00`~`07` 단계 실행기, `99_validate.py` 검증 골든셋, `run_embed.sh`
- **src/normalize/**, **src/extract/**, **src/render/** — 파싱 공통층 (인코딩·표·기간·재무)
- **src/chunk/** — 자족 청크 생성
- **src/index/** — bge-m3 임베딩 (`embed_prepare` → `embed_worker` → `embed_merge` → `verify_vectors`)
- **src/eval/** — dense+sparse 검색, 질문 세트 채점 (`ask`, `retriever`, `run_eval`, `sweep`)
- **parser/** — 이관 전 원본 파서 4종. 대조용 참고 원본
- **data/** — 파생 산출물 전부 (미추적, 아래 참고)
- **reports/** — 검증 CSV·MD
- **docs/** — 예외 매트릭스, 청킹·임베딩·검색 실측 노트

---

## 데이터 (전부 git 미추적)

| 경로 | 크기 | 내용 |
|------|------|------|
| `data/index/vectors/` | 6.5GB | `dense.f32.npy` (614,693×1024) · `sparse.npz` · `id_map.parquet` · `_work/` 샤드 |
| `data/processed/chunks_by_10_companies/` | 3.0GB | 임베딩 **입력** 청크 8개 (70개사, 617,380줄 → 대체본 2,802 제외 = 614,578) |
| `corpus/corrections/` | 5.7MB | 정정 위치 마킹 md 5개. pdf+html 문서의 유일한 파싱 결과다 |
| `data/processed/_archive/` | 282MB | 위 청크의 배포 zip |
| `data/processed/chunks.jsonl.gz` | 272MB | 이 레포 `06_build_chunks.py` 자체 청킹 산출물 |
| `data/interim/`, `data/baseline_md/` | ~1.1GB | doc.json, 기준선 md |

> **주의 — 두 청킹 결과는 다른 코퍼스다.** 지금 있는 벡터는 배포본(`chunks_by_10_companies/`,
> 70개사 614,693청크)으로 만든 것이고, 이 레포의 `06_build_chunks.py`가 만드는
> `chunks.jsonl.gz`는 별개다. 후자로 다시 임베딩하려면 `DART_CHUNKS_DIR`로 입력을 갈아끼운다.

---

## 기술

- Python 3.12
- 파싱: lxml, 자체 정규화층 (`src/normalize/`)
- 임베딩: **BAAI/bge-m3** (1024차원 dense + 250,002 lexical sparse), GPU 4장 병렬
- 검색: dense 코사인 + sparse 내적, 기본 가중 1:1
- 저장: numpy memmap + scipy CSR + parquet id_map (벡터 DB 없음)

---

## 더 읽을 것

- [docs/embedding_notes.md](docs/embedding_notes.md) — 임베딩 재실행 방법, 산출물 계약, 입력 필드 매핑
- [docs/retrieval_eval_notes.md](docs/retrieval_eval_notes.md) — 40문항 실측, 실패 사례, GPU 온도 관리
- [docs/chunking_notes.md](docs/chunking_notes.md) — 이 레포의 청킹 규칙
- [docs/chunking_by_10_companies.md](docs/chunking_by_10_companies.md) — 배포본 청킹 결과(현 벡터의 입력)
- [docs/exception_matrix.md](docs/exception_matrix.md) — 원문 예외 유형과 조치
- [docs/document_index.md](docs/document_index.md) — 문서 메타 인덱스 스펙
- **[docs/pitfalls.md](docs/pitfalls.md) — 주의사항. 레포를 만지기 전에 읽는다**
- [docs/md_index.md](docs/md_index.md) — md 파일 메타 인덱스 + **청킹하는 사람을 위한 임베딩 입력 계약**

---

*Internal research use.*


## 정리 링크
https://claude.ai/code/artifact/b8044591-c80f-417c-b2c0-ed7522cfffc3
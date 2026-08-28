# 공시 RAG — bge-m3 벡터 인덱스

청킹 결과는 [chunking_by_10_companies.md](chunking_by_10_companies.md), 질의 실험 환경은 [retrieval_eval_notes.md](retrieval_eval_notes.md) 참고.

> 이 단계는 `mirae/` 에서 이 레포로 합쳐졌다. 코드는 `src/index/`, 산출물은 `data/index/vectors/`,
> 임베딩 입력 청크는 `data/processed/chunks_by_10_companies/` 에 있다. 경로는 전부
> [src/index/paths.py](../src/index/paths.py) 한 곳에서 나온다.

## 재실행 방법

```bash
python -m src.index.embed_prepare                                   # 1. 청크 → _work/embed_texts.parquet + id_map.parquet
bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384    # 2. GPU 4장 병렬 임베딩 (중단 후 재실행하면 이어서 진행)
python -m src.index.embed_merge && python -m src.index.verify_vectors  # 3. 샤드 머지 + 검증
```

## 산출물

```
data/index/vectors/
├── dense.f32.npy        # (N, 1024) float32, L2 정규화. 행 순서 = 청크 소스 순서
├── id_map.parquet       # row → chunk_id + 하드필터 메타 + embed_sha1
├── sparse.npz           # scipy CSR (N, 250002), bge-m3 lexical weights
├── meta.json            # 모델·차원·max_length·소스 해시·처리 시간
└── _work/               # 샤드·체크포인트 (머지 후에도 보존)
```

## 주의

- `id_map.parquet`의 `row`가 유일한 계약이다. 청크 순서를 재정렬하지 말 것.
- bge-m3는 대칭 모델이다. 질의에 `query: ` 같은 접두사를 붙이지 말 것.
- 워커가 죽으면 해당 rank만 다시 띄우면 된다: `CUDA_VISIBLE_DEVICES=2 python -m src.index.embed_worker --rank 2 --world 4`
- 샤드 경계(`--world`)를 바꾸면 `_work/dense.shard*.npy`를 지우고 처음부터 다시 돌려야 한다.

### 입력 필드 매핑

지시서의 입력 계약과 실제 JSONL 필드명이 달라 아래와 같이 매핑했다 (`meta.json`의 `field_mapping`에도 기록).

| 계약 | 실제 소스 |
| --- | --- |
| `embed_text` | `embedding_text` (그대로 사용, 재조립 없음) |
| `corp_code` | `stock_code` (종목코드) |
| `doc_group` | `disclosure_type` |
| `base_year` | `int(receipt_no[:4])` — 소스에 없어 접수번호에서 추출 |
| `rcept_dt` | `receipt_no[:8]` — 동일 |

`base_year`/`rcept_dt`는 **공시 접수일** 기준이지 사업연도가 아니다. 연도 하드필터를 걸 때 유의할 것.

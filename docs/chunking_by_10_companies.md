# 공시 RAG 청킹 결과 (배포본 70개사)

> 현재 `data/index/vectors/` 의 **입력**이 된 청킹 결과다. 파일은
> `data/processed/chunks_by_10_companies/` 에 있다. 이 레포가 직접 만드는 청킹
> (`06_build_chunks.py` → `data/processed/chunks.jsonl.gz`)은 별개이며,
> 규칙은 [chunking_notes.md](chunking_notes.md) 를 본다.

## 결과

- 원본 경로: `/Users/yang-gayeong/Downloads/rag`
- 입력 문서: 4,616개 Markdown 파일
- 출력: `disclosure_chunks.jsonl`
- 청크: 614,693개
- 출력 크기: 약 3.0GB
- 최대 본문 길이: 1,600자
- 긴 문단 분할 중첩: 200자

## 청킹 방식

Markdown 제목 계층을 기준으로 섹션을 보존하고, 문단·목록은 의미 블록으로 묶었습니다. 표는 헤더를 각 분할 조각에 반복하여 열의 의미가 사라지지 않도록 했습니다. 1,600자를 넘는 긴 블록만 문장 또는 줄 경계에서 추가 분할하고 200자를 중첩했습니다.

원본 파일은 수정하지 않았습니다.

## JSONL 주요 필드

- `chunk_id`: 벡터 DB 기본키로 사용할 수 있는 고유 ID
- `doc_id`: 접수번호와 원본 파일명 해시를 결합한 문서 ID
- `chunk_index`: 문서 내부 청크 순번
- `company`, `stock_code`, `industry`: 기업 메타데이터
- `disclosure_type`: `periodic`, `exchange`, `major`, `holding`
- `receipt_no`, `form_code`: 파일명에서 추출한 접수번호와 선택적 서식 코드
- `document_title`: 공시 문서 제목
- `section_path`: Markdown 제목 계층
- `content`: 원문 청크
- `embedding_text`: 회사명, 문서명, 섹션 경로를 앞에 붙인 임베딩 입력용 텍스트
- `char_count`: `content` 글자 수
- `approx_tokens`: 참고용 추정치이며 실제 임베딩 모델 토크나이저 값은 아님

## 검증 결과

- JSON 파싱 오류: 0
- 중복 `chunk_id`: 0
- 필수 필드 누락: 0
- 길이 불일치: 0
- 최대 길이 초과: 0
- 고유 문서 수: 4,616

공시 유형별 청크 수:

- `periodic`: 577,124
- `exchange`: 4,307
- `major`: 4,319
- `holding`: 28,943

## 임베딩 권장 방식

임베딩에는 `embedding_text`를 사용하고, 벡터 DB의 표시·인용 원문에는 `content`를 사용합니다. 검색 시 회사명, 종목코드, 공시 유형, 접수번호를 필터 메타데이터로 저장하면 공시 질의의 정확도가 좋아집니다.

실제 임베딩 모델이 정해지면 그 모델의 토크나이저로 `embedding_text` 길이를 다시 검사하는 것이 좋습니다.


---

# 2차 — 정정공시 반영 (2026-08-28)

정정사항의 **위치**가 표시되지 않은 공시가 있어서, 원본을 마커 붙여 다시 청킹하고
정정사항을 따로 청킹해 받았다. XML 이 없어 이 레포가 못 읽던 pdf+html 3건도 여기서 들어왔다.

## 파일

```
data/processed/chunks_by_10_companies/
├── disclosure_chunks_companies_001-010.jsonl … 061-070.jsonl   1차 7개 (614,693줄, 그대로)
├── disclosure_chunks_pdf_docs.jsonl                            2차 1개 (2,687줄)
├── manifest.json                                               2차 (skipped_doc_ids 포함)
└── manifest_v1.json                                            1차 (대조용)

corpus/corrections/                                             위 청크의 입력 md 5개
├── KB금융_periodic_20260313001191_마커.md          3.9MB  원본 + 정정 위치 마커
├── KB금융_정정사항_20260619000667.md               4.0KB  정정사항 (pdf+html)
├── 한화오션_periodic_20240513000844_마커.md         681KB  원본 + 정정 위치 마커
├── 한화오션_정정사항_20240514001522.md              4.0KB  정정사항 (pdf+html)
└── 한화에어로스페이스_periodic_20260513000860.md    1.3MB  분기보고서 (pdf+html)
```

`disclosure_chunks_*` 를 정렬하면 `pdf_docs` 가 맨 뒤에 온다(c < p). 1차 7개 파일의
row 번호가 그대로 유지된다.

## 문서 5건

| doc_id | 회사 | doc_role | source_format | 청크 | 대체 대상 |
| --- | --- | --- | --- | ---: | --- |
| `20260313001191-7a4d61dc97` | KB금융 | original | xml | 1,678 | `…-8e06344caf` |
| `20240513000844-b40f169fc6` | 한화오션 | original | xml | 344 | `…-46bde36967` |
| `20260513000860-4930c4ae50` | 한화에어로스페이스 | original | **pdf+html** | 657 | — |
| `20260619000667-2598b34a2b` | KB금융 | correction_delta | **pdf+html** | 4 | — |
| `20240514001522-fcbf1d25f1` | 한화오션 | correction_delta | **pdf+html** | 4 | — |

`original` 둘은 마커를 붙여 다시 만든 것이라 **옛 버전을 대체한다**. 옛 버전은 1차 파일에
그대로 남아 있으므로 임베딩 단계에서 걸러야 한다 — `manifest.json` 의 `skipped_doc_ids`
2건 / 2,802행이 그것이다.

```
617,380줄  -  2,802(대체됨)  =  614,578행
```

`embed_prepare` 와 `chunk_store` 가 `paths.skipped_doc_ids()` 로 **같은 규칙**을 쓴다.
한쪽만 거르면 row 순서가 어긋나 `chunk_store --build` 가 "행 수 불일치"로 죽는다.

## 새 필드

`pdf_docs.jsonl` 만 갖는다. 1차 7개 파일은 옛 스키마 그대로다.

| 필드 | 뜻 |
| --- | --- |
| `doc_role` | `original` / `correction_delta` |
| `is_correction` | 정정공시인가 |
| `replaces_doc_id` | 이 문서가 대체하는 옛 doc_id |
| `replaces_source_file` | 그 옛 문서의 md 파일명 |
| `is_superseded` | **밀려났는가.** 있으면 `_mark_superseded` 휴리스틱보다 우선한다 |
| `source_format` | `xml` / `pdf+html` |
| `base_year` · `base_month` | **사업연도.** 접수연도가 아니다 |
| `corp_code` · `sector` · `doc_subtype` · `report_nm` · `rcept_dt` · `listed_name` | 문서 메타 |

`base_year` 가 특히 중요하다. 옛 스키마에는 없어서 임베딩 인덱스가
`int(receipt_no[:4])`(접수연도)로 대신 채우고 있다. 전량 재청킹하면 진짜 사업연도로
연도 필터를 걸 수 있다.

## 검사 결과

```
python scripts/09_preflight_chunks.py data/processed/chunks_by_10_companies

  [PASS] 필수 6필드 전부 채워짐
  [PASS] chunk_id 고유 (617,380개)
  [PASS] chunk_id 구분자 ':'
  문서 커버리지  4,204 / 4,204   결손 0        ← pdf+html 3건이 여기서 메워졌다
```

## 재임베딩 결과 (2026-08-28)

```
GPUS=0,1,2,3 DART_VECTORS_DIR=data/index/vectors_v2   bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384
```

| | v1 (기존) | v2 (정정 반영) |
| --- | ---: | ---: |
| 행 | 614,693 | **614,578** |
| sparse nnz | 102,720,240 | 102,744,812 |
| 벽시계 | 40분 (batch_tokens 131,072) | 55분 (16,384) |
| 검증 | — | **10/10 PASS** |

`verify_vectors` 전량 통과 — dense (614578, 1024), L2 norm 0.999614~1.000371,
NaN/영벡터 0, embed_sha1 614,578행 전량 일치, chunk_id 순서 일치, sparse 빈 행 0,
무작위 20건 재임베딩 코사인 min 0.999416.

`chunk_store --build`:

```
manifest 가 대체됐다고 표시한 2개 문서 / 2,802행 건너뜀
청커가 is_superseded 를 직접 단 문서 5개 — 추측 대신 그 값을 쓴다
정정 재제출로 밀려난 옛 정기보고서: 14,442행 (2.35%)
chunk_id 614,578행 전량 일치
```

### 48문항 A/B — 지표가 완전히 같다

같은 질문 세트를 두 인덱스에 돌렸다.

| | v1 | v2 |
| --- | ---: | ---: |
| 회사@1 | 100% | 100% |
| 섹션@1 | 90.0% | 90.0% |
| 섹션@5 | 95.0% | 95.0% |
| strict@1 | 90.6% | 90.6% |
| 비교질의 개체커버(분할) | 100% | 100% |

**상위 5건이 달라진 질문 0개 / 88개 (질문×모드).** 신규 문서 청크가 결과에 등장한 것도 0건이다.

회귀가 없다는 뜻이지 개선됐다는 뜻이 아니다 — **현재 질문 세트가 바뀐 5개 문서를 건드리지
않는다.** 새 문서를 겨냥해 직접 찔러보면 정상적으로 올라온다:

```
Q: KB금융 사업보고서 정정사항 정정 내용   (회사필터=KB금융)
  1. 0.9713  [KB금융] 제18기 사업보고서 (2025.12) 정정사항          ← 신규
  2. 0.9523  … > 정정 #1                                          ← 신규
  3. 0.9453  … > 정정 #3                                          ← 신규
  5. 0.9360  … > 정정 #2                                          ← 신규

Q: 한화에어로스페이스 2026년 1분기 분기보고서 요약재무정보
  5. 0.9637  [한화에어로스페이스] 분기보고서 (2026.03)              ← 신규(XML 없던 문서)
```

정정공시·pdf 문서를 겨냥한 질문을 `src/eval/questions.jsonl` 에 추가해야 이 변화가
지표로 잡힌다. **지금 48문항으로는 측정되지 않는다.**

### 정본 승격 (2026-08-28)

A/B 가 끝나 비교 기준으로서의 역할이 끝났으므로 v2 를 정본으로 올리고 옛 인덱스를 지웠다.

```
data/index/vectors/          614,578행. 이제 DART_VECTORS_DIR 없이 그냥 쓴다
data/index/vectors_v1_old/   삭제 (6.5GB)
vectors/_work/dense.shard*   삭제 (3.8GB) — 머지·검증이 끝나 다시 쓸 일이 없다
```

`_work/embed_texts.parquet`(240MB)은 남겼다. 재임베딩 없이 다시 머지하거나
`verify_vectors` 를 돌릴 때 쓴다.

```
13GB  →  2.9GB
```

승격 후 무결성 재확인: dense (614578, 1024) · sparse nnz 102,744,812 ·
id_map 614,578 · text_offsets 614,578 — 전부 일치. 환경변수 없이 질의 정상 동작.

### 청크 파일 정리 (2026-08-29)

정정 마킹 새 버전으로 대체된 옛 문서를 **파일에서 실제로 뺐다.** 그동안은 파일에 남아
있고 임베딩만 안 되는 상태였다.

```bash
python scripts/11_compact_chunks.py --out data/processed/chunks_v2
```

```
617,380줄  →  614,578줄   (2,802줄 제거)
  001-010  85,436 → 83,013  (KB금융 옛 버전 2,423행)
  061-070  83,441 → 83,062  (한화오션 옛 버전   379행)
  나머지 6개는 바이트 그대로 복사
[PASS] chunk_id 614,578행이 id_map 과 순서까지 일치
```

이제 청크 줄 수 = 벡터 행 수다. `manifest.json` 의 `skipped_doc_ids` 는 비었고,
무엇을 왜 뺐는지는 `compaction` 블록과 `meta.json` 의 `compaction_history` 에 남는다.

딸려 온 것 — 스크립트가 자동으로 처리한다

| | |
| --- | --- |
| `text_offsets.npz` | 바이트 위치라 줄을 빼면 그 뒤가 전부 밀린다. 재빌드 필수 |
| `meta.json` 의 `chunks_file_sha1` | 임베딩 당시 입력의 지문. 갱신해야 `--verify` 가 통과한다 |
| 배포 패키지 | `SHA256SUMS` 가 달라진다. 다시 만들어야 한다 (6.07GB, 33개 파일) |

**재임베딩은 필요 없다.** 뺀 줄은 애초에 벡터에 들어간 적이 없다.

검증: preflight 커버리지 4,204/4,204 결손 0 · 검색 정상(정정사항이 1위, 본문도 맞다).

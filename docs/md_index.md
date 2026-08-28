# md 파일 메타 인덱스

`data/index/md_files.jsonl` — **md 파일 하나당 한 줄, 4,616줄.**
[scripts/05b_build_md_index.py](../scripts/05b_build_md_index.py)가 만든다.

md 파일 이름만 손에 들고 "이건 어느 회사 무슨 공시인가"를 답하기 위한 표다.
청킹할 때 각 청크에 실을 메타를 여기서 그대로 뽑아 쓴다.

## 왜 05로 부족한가

| 파일 | 단위 | 줄 수 | 두께 |
|---|---|---|---|
| `baseline/index.jsonl` | **md 파일** | 4,616 | 얇다 — 해시·바이트·회사명·문서군까지 |
| `data/index/documents.jsonl` | 문서 | 4,204 | 두껍다 — 업종·섹터·기수·목차 |
| **`data/index/md_files.jsonl`** | **md 파일** | **4,616** | **두껍다** ← 이 파일 |

정기보고서는 본문·첨부로 쪼개져 한 문서가 md 여러 개로 나온다(분할본 415개).
그래서 문서 단위 인덱스로는 md 파일에 메타를 붙일 수 없다.

```
periodic_20240228008553      ← 문서 1개 (documents.jsonl)
├── ..._00760.md             ← md 3개 (md_files.jsonl)
├── ..._00761.md
└── ....md
```

## 값을 새로 만들지 않는다

전부 아래 넷을 조인한 것이다. `--verify`가 4,616줄 × 12필드를 출처와 재대조한다.

```
baseline/index.jsonl              해시·바이트·원문경로
data/index/documents.jsonl        업종·섹터·목차·기수·dart_url
corpus/manifest.jsonl             사업연도·file_format
data/interim/alias_registry.json  회사명 별칭
```

예외는 둘이고 근거가 있다.

- `document_title` = `"[{company}] {report_nm}"` — 배포본 청크의 `document_title` 계약을 재현. 404문서 대조 불일치 0건
- `is_part` / `part_suffix` — `key`가 `doc_id`로 시작하는지로 갈림. 4,616건 예외 0

```bash
python scripts/05b_build_md_index.py            # 생성
python scripts/05b_build_md_index.py --verify   # 출처와 전량 재대조
```

## 필드

### md 파일 자신

| 필드 | 예시 | 설명 |
|---|---|---|
| `md_file` | `우리기술_exchange_20230102900376.md` | **조인 키.** 파서가 내놓는 파일명 그대로 |
| `key` | `exchange_20230102900376` | md 파일 식별자 (분할본이면 suffix 포함) |
| `doc_id` | `exchange_20230102900376` | 문서 식별자. 분할본 여럿이 이걸 공유 |
| `is_part` / `part_suffix` / `n_parts` | `true` / `00760` / `3` | 분할본 여부 |
| `source_path` | `corpus/raw/exchange/…/20230102900376.xml` | 원문 XML |
| `md_bytes` | `1524` | |
| `full_sha256` / `body_sha256` | `85877e89…` | 회귀 기준선 해시 |
| `header_split_ok` | `true` | 헤더/본문 분리 성공 여부 |

### 회사

`company`, `company_aliases`, `corp_code`(DART 고유번호 8자리), `stock_code`(종목코드 6자리), `industry`, `sector`

### 공시

`disclosure_type`(periodic/exchange/major/holding), `doc_subtype`, `report_nm`, `document_title`, `is_correction`, `receipt_no`, `rcept_dt`, `base_year`, `base_month`, `dart_url`, `file_format`

### 내용

`n_blocks`, `n_tables`, `toc`, `periods`, `periods_unresolved`, `parse_confidence`, `status`

---

## 청킹하는 사람에게 — 임베딩 입력 계약

[src/index/embed_prepare.py](../src/index/embed_prepare.py)가 요구하는 필수 필드다.
**하나라도 없거나 비면 임베딩이 시작 전에 중단된다**(임의로 채우지 않는다).

| 청크에 넣을 이름 | 이 인덱스에서 | 비고 |
|---|---|---|
| `chunk_id` | (청커가 생성) | **아래 주의 참고** |
| `embedding_text` | (청커가 생성) | 그대로 모델에 들어감 |
| `doc_id` | `doc_id` | |
| `stock_code` | `stock_code` | 하드필터 `corp_code`가 됨 |
| `disclosure_type` | `disclosure_type` | |
| `receipt_no` | `receipt_no` | 14자리. 앞 8자리가 숫자여야 함 |

표시용으로 같이 넣으면 좋은 것: `content`, `company`, `document_title`, `section_path`(청커 생성).

### 주의 1 — `chunk_id`는 `{doc_id}:{순번}` 형식이어야 한다

[src/eval/chunk_store.py:72](../src/eval/chunk_store.py#L72)가 **콜론으로** 문서를 가른다.

```python
doc = rec["chunk_id"].split(":")[0]
```

`#`이나 다른 구분자를 쓰면 에러 없이 조용히 깨진다 — 정정 재제출본을 걸러내는
`_mark_superseded`가 통째로 무력화된다(현재 3.7%가 이 규칙으로 밀려나 있다).

분할본이 있는 문서는 `chunk_id`에 `key`(suffix 포함)를 쓰되 콜론 앞부분이
같은 문서끼리 묶이도록 해야 한다.

### 주의 2 — `base_year`는 두 가지가 있다

| 출처 | 뜻 |
|---|---|
| 이 인덱스의 `base_year` | **사업연도.** 정기보고서에만 있음(1,054건), 나머지는 `null` |
| 임베딩 인덱스의 `base_year` | **접수연도** — `int(receipt_no[:4])`로 기계 추출한 값 |

현재 `id_map.parquet`의 `base_year`는 후자다. 연도 필터를 걸 때 사업연도가
필요하면 이 인덱스의 값을 청크에 실어 보내야 한다.

### 주의 3 — 파일명 정렬 순서 = 벡터 행 번호

`sorted(glob)` 결과가 곧 row 순서 계약이다. 파일을 쪼개는 단위나 이름을 바꾸면
기존 벡터와 행이 어긋난다.

### 먼저 이것부터

4시간짜리 임베딩을 돌리기 전에 계약 위반을 30초 안에 잡는다.

```bash
DART_CHUNKS_DIR=<청크경로> python -m src.index.embed_prepare --limit 5000
```

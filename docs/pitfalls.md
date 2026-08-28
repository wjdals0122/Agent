# 주의사항 — 밟으면 아픈 것들

이 레포를 만지기 전에 읽는다. 실제로 밟았거나, 코드를 읽다 발견한 것만 적는다.
심각도 순서다.

---

## 1. 기준선을 날리는 명령

### `python scripts/00_freeze_baseline.py` — 그냥 실행하면 안 된다

`--force` 없이도 **`baseline/hash/` 4,616개와 `baseline/index.jsonl`을 전부 덮어쓴다.**
조건 없이 새로 쓴다:

```python
for r in records:
    if r['status'] != 'ok': continue
    hp = os.path.join(P.BASELINE_HASH_DIR, r['key'] + '.md.sha256')
    with open(hp, 'w', ...)          # ← 조건 없음
```

파서를 고친 뒤 이걸 돌리면 **지금 코드의 출력이 새 정답이 된다.** 버그가 있어도 그 버그가
정답이 되고, 무엇이 달라졌는지 비교할 대상이 사라진다.

**md가 필요할 뿐이라면 이걸 쓴다:**

```bash
python scripts/99_validate.py --baseline --write-md
```

`data/verify/md/`에 4,616개를 쓰고, `baseline/`은 **읽기만** 한다.
동시에 지문을 전량 대조하므로 "이 md가 기준선과 같은 세대인가"까지 답이 나온다.
(실측 71.4초 / 1.1GB)

0단계를 정말 다시 세워야 할 때는, 무엇이 왜 바뀌는지 확인하고 감사 기록을 남긴다 —
`reports/baseline_correction_*.csv`가 그 자리다.

---

## 2. 에러 없이 조용히 깨지는 것

에러가 나면 다행이다. 아래는 **아무 말 없이 틀린 결과를 낸다.**

### `chunk_id`는 반드시 `{doc_id}:{순번}` 형식

[src/eval/chunk_store.py:72](../src/eval/chunk_store.py#L72)가 **콜론으로** 문서를 가른다.

```python
doc = rec["chunk_id"].split(":")[0]
```

`#`이나 다른 구분자를 쓰면 `split`이 아무것도 못 자르고, 정정 재제출본을 걸러내는
`_mark_superseded`가 통째로 무력화된다. 현재 3.7%가 이 규칙으로 밀려나 있다.

청크가 `is_superseded` 를 직접 달고 있으면 그 값을 쓰고 추측하지 않는다(2026-08-28).
필드가 없는 옛 스키마 행에만 위 휴리스틱이 적용된다.

- 배포본 청크: `20230629000567-758fd852d5:0298` — 맞다
- 이 레포 06단계 청크: `exchange_20230102900376#0000` — **`#`이다**

두 청커를 섞어 쓸 때 특히 위험하다.

### 파일명 정렬 순서 = 벡터 행 번호

`sorted(glob)` 결과가 곧 row 순서 계약이다. `id_map.parquet`의 `row`만이 벡터와 조각을
잇는다. 청크 파일을 쪼개는 단위나 이름을 바꾸면 **모든 검색 결과가 엉뚱한 문서를 가리킨다.**
샤드 경계(`--world`)를 바꾸면 `_work/dense.shard*.npy`를 지우고 처음부터 다시 돌려야 한다.

### `base_year`가 두 종류다

| 출처 | 뜻 |
|---|---|
| `md_files.jsonl` / `manifest.jsonl` | **사업연도.** 정기보고서에만 있음(1,466줄), 나머지 `null` |
| `id_map.parquet` (임베딩 인덱스) | **접수연도** — `int(receipt_no[:4])`로 기계 추출 |

지금 연도 하드필터는 후자로 걸린다. 사업연도로 거르려면 그 값을 청크에 실어 보내야 한다.
`rcept_dt`도 마찬가지로 접수일이다.

### md 파일의 세대

파서가 바뀌면 md도 바뀐다. 다른 PC에서 만든 md를 받아 쓸 때는 세대가 같은지 확인한다.

```bash
python -c "
import hashlib, json, os
BASE = {json.loads(l)['out_file']: json.loads(l)['full_sha256']
        for l in open('baseline/index.jsonl', encoding='utf-8')}
MD = r'<받은 md 폴더>'
bad = [f for f in os.listdir(MD) if f in BASE and
       hashlib.sha256(open(os.path.join(MD,f),'rb').read()).hexdigest() != BASE[f]]
print('불일치', len(bad), '/', len(BASE)); print(bad[:10])
"
```

*(2026-08-27 기준: 현재 작업 트리 파서의 md는 기준선과 4,616/4,616 동일하다.)*

### 재임베딩은 기본값으로 기존 벡터를 덮어쓴다

입력만 `DART_CHUNKS_DIR`로 바꿔 돌리면 출력은 그대로 `data/index/vectors/`로 가서
61만 벡터와 40문항 실측 기준선(섹션@1 90% / strict@1 91%)이 사라진다.
**출력 경로를 반드시 같이 바꾼다:**

```bash
export DART_CHUNKS_DIR=data/processed/chunks_v2
export DART_VECTORS_DIR=data/index/vectors_v2
python -m src.index.embed_prepare && bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384
python -m src.index.embed_merge && python -m src.eval.chunk_store --build
```

`chunk_store --build`도 같은 환경변수를 봐야 한다 — 오프셋 사이드카가 벡터 폴더에 들어간다.

### 청크 파일에 '대체된 옛 문서'가 남아 있다

정정 위치를 마킹한 새 버전이 들어오면 옛 버전은 청크 파일에서 **지워지지 않는다.**
`manifest.json` 의 `skipped_doc_ids` 가 그것을 가리킨다.

```json
"skipped_doc_ids": ["20240513000844-46bde36967", "20260313001191-8e06344caf"],
"skipped_row_count": 2802
```

거르지 않으면 같은 보고서가 두 벌 색인된다. `embed_prepare` 와 `chunk_store` 가
**같은 규칙으로** 건너뛴다 — 한쪽만 거르면 row 순서가 어긋나 `chunk_store --build` 가
"행 수 불일치"로 죽는다.

```
617,380줄  -  2,802(대체됨)  =  614,578행 임베딩
```

### `parse_report.jsonl`을 두 단계가 공유한다

`00_freeze_baseline.py`와 `03_build_docjson.py`가 **같은 경로**에 쓴다.
03이 나중에 돌면 00의 기록(`no_source_xml` 3건 포함)이 사라진다.
필드 모양이 다르니(`key/full_sha256` vs `stage/parts/elapsed`) 어느 쪽인지는 구분된다.

---

## 3. 즉시 멈추는 것 (좋은 실패)

[src/index/embed_prepare.py](../src/index/embed_prepare.py)는 계약 위반을 **임의로 채우지 않고
종료**한다. 아래는 4시간짜리 임베딩을 시작하기 전에 잡힌다.

| 조건 | 결과 |
|---|---|
| 필수 6필드 중 하나라도 없거나 빈 값 | 중단 (`chunk_id`, `embedding_text`, `doc_id`, `stock_code`, `disclosure_type`, `receipt_no`) |
| `chunk_id` 중복 1건이라도 | 중단 |
| `receipt_no`가 8자리 미만이거나 앞 8자가 숫자 아님 | 중단 |
| JSON 파싱 실패 | 중단 (파일명:줄번호 출력) |

**임베딩 전에 이걸 먼저 돌린다:**

```bash
DART_CHUNKS_DIR=<청크경로> python -m src.index.embed_prepare --limit 5000
```

30초면 계약 위반이 드러난다.

---

## 4. 환경

### `--jobs` 기본값이 윈도우에서 크래시한다 (수정됨)

```
ValueError: max_workers must be <= 61
```

윈도우 `ProcessPoolExecutor`의 워커 상한이 61인데 기본값이 `cpu_count() - 1`이었다.
64코어 기계에서 63이 들어가 **시작하자마자 죽는다.** 병렬 스크립트 6개가 전부 같았다
(`00`, `01`, `03`, `06`, `07`, `99`). `min(61, ...)`로 막았다.

### 한글 경로의 NFC / NFD

같은 글자가 두 가지 바이트열로 존재한다. 맥·구글드라이브·zip을 거친 코퍼스는 NFD로
저장되는데 `manifest.jsonl`과 `baseline/index.jsonl`은 NFC로 적혀 있다.
윈도우는 둘을 같은 이름으로 봐 주지 않아서 **폴더가 실제로 있는데도 없다고 나온다**
(실측: 4,204개 중 4,054개).

기록에 남는 경로는 언제나 NFC(`P.rel`), 디스크를 열 때만 실제 저장형을 찾는다(`P.on_disk`).
셸에서 `ls "corpus/raw/periodic/한화오션/…"`이 "No such file"이면 이 문제다.

### 콘솔 인코딩

`src/eval/*`의 진입점은 맨 위에서 `sys.stdout.reconfigure(encoding="utf-8")`를 건다.
직접 스크립트를 쓸 때는 `PYTHONIOENCODING=utf-8`을 붙이지 않으면 `cp949` 인코딩 에러가 난다
(특히 `—`, `→` 같은 문자).

### 백그라운드 실행에 파이프를 걸지 말 것

`python … | tail -25`로 돌리면 **파이프의 종료 코드가 보고되어 실패가 성공으로 보인다.**
실제로 이걸로 크래시를 놓쳤다.

---

## 5. 데이터 취급

### 잃어버리면 안 되는 건 둘뿐

```
corpus/raw/     원문 XML
baseline/       회귀 기준선 지문
```

나머지 — md, doc.json, 청크, 벡터 — 는 전부 그 둘에서 다시 만들어진다.
`.gitignore`가 이 기준으로 짜여 있다.

### 청크 원본 JSONL은 계속 보관해야 한다

벡터 파일에는 표시용 원문(`content`)이 없다. `chunk_store`가 원본 JSONL을 바이트 오프셋으로
seek해서 읽는다. 파일을 옮기거나 다시 쓰면 `text_offsets.npz`를 재빌드해야 한다
(`python -m src.eval.chunk_store --build`, 약 18초).

없으면 검색 순위는 나오는데 **근거 본문을 못 보여준다.**

### md 본체(1.1GB)는 버려도 된다

지문이 파일이 아니라 **메모리 안의 문자열**에서 나오기 때문이다
(`sha256_text(text)`, `sha256_file(path)`가 아니다). 그래서 md 파일은 기준선이 아니라
부산물이고, 저장된 md를 읽는 코드는 한 줄도 없다.

단, **md에서 청킹하는 사람이 생기면 이야기가 달라진다.** 그때는 남겨야 한다.

---

## 6. md에서 청킹할 때 잃는 것

md는 이미 키와 값의 경계가 뭉개진 뒤다. doc.json 기반 청커와 실측 비교(각 40만 청크 표본):

| | 레포 청커 (doc.json) | 배포본 청커 (md) |
|---|---|---|
| 표 청크 중 단위 보유 | **73.8%** (`unit` 필드) | **36.1%** (본문 "단위:" 문자열) |
| `periods` (제 40 기 1분기) | 45,030개 | **스키마에 없음** |
| `period_dates` (실제 날짜) | 30,689개 | **스키마에 없음** |
| `unit_source` | 있음 | 없음 |

*(표를 세는 기준이 서로 달라 절대 비교는 아니다. 방향만 본다.)*

```
doc.json  ["kv", ["2. 계약내역", "확정 계약금액"], "2,128,000,000", [섹션경로]]
md        - 2. 계약내역 > 확정 계약금액: 2,128,000,000
                                          ↑ 키와 값의 경계를 되찾을 수 없다
```

표 밖에 떠 있던 "단위: 백만원"을 어느 표에 붙일지도 doc.json은 형제 위치로 알지만 md는 모른다.
정규식으로 일부는 복구되지만 원리상 100%는 안 된다.

md 기반 청킹 결과를 받는다면, **같은 40문항으로 기존 벡터와 비교한 뒤에** 교체 여부를 정한다.

---

## 7. 알려진 결손

| 항목 | 상태 |
|---|---|
| ~~PDF 문서 3건~~ | **해소(2026-08-28).** KB금융·한화오션·한화에어로스페이스를 별도 청킹해 `disclosure_chunks_pdf_docs.jsonl` 로 받았다. 문서 커버리지 4,204 / 4,204. 다만 이 레포의 `03_build_docjson.py` 는 여전히 못 읽는다 — doc.json 은 없고 청크만 있다 |
| 비교 질의 | "삼성전자와 SK하이닉스 매출액 비교" → 상위 20건이 전부 한쪽 회사. 질의 벡터가 두 개체의 혼합이라 승자독식 |
| 답변 생성 | LLM 미연결. 검색 근거까지만 |
| 청커 2종 병존 | 배포본(61.5만) vs 레포 06단계(129만, 미임베딩). `meta.json`의 `chunks_file_sha1`이 사후 추적은 해준다 |
| 스키마 2종 혼재 | `pdf_docs.jsonl` 2,687행만 `doc_role`·`is_superseded`·`base_year`(사업연도) 등 신규 필드를 갖는다. 나머지 61.4만행은 옛 스키마다. `is_superseded` 로 전면 전환하려면 전량 재청킹이 필요하다 |
| 환각 검증 | `--facts`는 통과했으나 네 문서군 전수는 아직 |

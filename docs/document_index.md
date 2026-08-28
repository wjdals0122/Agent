# 문서 메타 인덱스 — `data/index/documents.jsonl`

문서 하나당 한 줄. **4,204줄 / 4.6MB.** 264MB짜리 `doc.json` 을 열지 않고도
"어떤 문서가 있고, 무엇을 담고 있고, 어디서 원문을 보는지"를 훑을 수 있다.

```bash
python scripts/05_build_doc_index.py      # 만들기
python scripts/99_validate.py --index     # 지어낸 값이 없는지 검증
```

---

## 왜 md 앞머리(front matter)가 아닌가

세 가지 이유다.

1. **같은 사실을 두 군데 적으면 갈라진다.** 식별 정보의 출처는
   `corpus/manifest.jsonl`, 파생 정보의 출처는 `data/interim/docs/*.json.gz` 다.
   이 파일은 그 둘을 옮겨 적는 것이 아니라 **조인해서 얇게 편 것**이고,
   값을 새로 만들지 않는다.
2. **md 는 0단계 회귀 기준선이다.** 헤더를 바꾸면 4,616건의 `full_sha256` 이
   전부 바뀐다. 얻는 것 없이 기준선만 흔든다.
3. **지금 md 를 읽는 소비자가 없다.** LLM 에 가는 것은 조각
   (`data/processed/chunks.jsonl.gz`)이고, 조각은 이미 본문 첫 줄에
   회사·보고서·접수일·섹션경로·단위·기수를 달고 있다. md 는 회귀 기준선이자
   사람이 읽는 산출물이다.

md 자체를 외부에 통째로 넘겨야 하는 소비자가 생기면, 그때 이 인덱스에서
front matter 를 **파생**시키면 된다. 사실의 출처는 그대로 한 곳이다.

---

## 필드

### 식별 — `manifest` 에서 그대로

| 필드 | 예 | 비고 |
|---|---|---|
| `doc_id` | `periodic_20230515002335` | 열쇠 |
| `corp_name` `corp_code` `stock_code` | `삼성전자` `00126380` `005930` | |
| `industry` `sector` | `IT` `반도체·전자부품` | 업종 필터 |
| `listed_name` | | `corp_name` 과 **다를 때만** |
| `flr_nm` | `미래에셋자산운용` | 제출인. `corp_name` 과 **다를 때만** — 대량보유보고서는 제출인이 회사가 아니다 |
| `doc_group` `doc_subtype` `report_nm` | `periodic` `quarter` `분기보고서 (2023.03)` | |
| `is_correction` | `false` | 정정공시 여부는 답의 유효성을 바꾼다 |
| `rcept_no` `rcept_dt` | `20230515002335` `2023-05-15` | 날짜는 ISO 로 편다 |
| `base_year` `base_month` | `2023` `3` | manifest 에 있는 문서만(1,054건) |
| `file_path` | `raw/periodic/삼성전자/…` | 원문 폴더 |

### 근거

| 필드 | 비고 |
|---|---|
| `dart_url` | `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}` — 인용·검증용 |
| `source_paths` | 실제 원문 XML 경로. periodic 은 한 접수번호에 최대 3개 |
| `n_parts` | 원문 파일 수 |

### 내용 — `doc.json` 에서 파생

| 필드 | 예 | 왜 |
|---|---|---|
| `toc` | `["I. 회사의 개요", "II. 사업의 내용", …]` | **h2만.** LLM 이 "어디를 봐야 하나"에 즉답. 문서당 최대 23개 |
| `n_tables` `n_blocks` | `580` `4,127` | 규모 감 |
| `financials` | `["{XBRL}BS_S", "{XBRL}IS_S1", …]` | 재무제표 유무와 종류 |
| `periods` | `{"제55기 1분기": "2023-01-01 ~ 2023-03-31"}` | **E7 결과.** 기수 라벨만 보고 연도를 못 맞히는 문제를 여기서 해결 |
| `corrections` | `{"pairs": 12, "changed": 3}` | 정정공시에서 실제로 몇 개가 바뀌었나 |

### 신뢰도

| 필드 | 왜 |
|---|---|
| `status` | `ok` / `no_source_xml`. 원문이 없는 3건도 **빼지 않고 남긴다** — 조용히 사라진 문서와 애초에 없는 문서는 다른 사실이다 |
| `parse_confidence` | `{"high": 7, "low": 3}` — {XBRL} 그룹 등급 |
| `periods_unresolved` | 날짜를 못 이은 기수 수. 몇 개인지 밝혀 두면 LLM 이 추측하지 않는다 |

`toc` 는 h2 만 담는다. h3 는 문서당 29개까지 늘어나 인덱스가 뚱뚱해진다
(`docs/chunking_notes.md` 참조).

---

## 일부러 안 넣은 것

| 안 넣은 것 | 이유 |
|---|---|
| 생성 시각·파이프라인 버전 | 돌릴 때마다 값이 바뀌어 멱등성과 회귀 검증이 무너진다. 손으로 올리는 `ver` 정수 때문에 이미 한 번 데였다([03_build_docjson.py](../scripts/03_build_docjson.py) 머리말) |
| 동의어/검색어 | md 헤더에 이미 있다. 중복은 갈라진다 |
| h3 이하 목차 | 크기 대비 효용이 낮다 |
| 예외 처리 건수(E1~E8) | LLM 에겐 노이즈다. `reports/exception_summary.md` 에 있다 |
| 못 이은 기수의 추정 날짜 | 문서가 말하지 않은 것이다. 개수만 남긴다 |

---

## 검증 — `99_validate.py --index`

**4,204 / 4,204 PASS** (목차 26,736마디 / 기수 5,807건 대조).

보는 것

- manifest 유래 필드가 manifest 와 **글자 그대로** 같은가
- 목차의 각 마디가 `doc.json` 에 실제 h2 제목으로 있는가
- 기수 사전의 각 값이 `doc.json` 의 `periods` 에 실제로 있는가
- `n_tables` 가 `doc.json` 의 표 수 합과 같은가
- manifest 의 문서가 하나도 빠지지 않았는가 (원문 없는 3건 포함)

`facts` 검사와 같은 질문을 인덱스에 던지는 것이다 — **지어내지 않았는가.**
답하지 않는 질문: 이 인덱스가 **충분한가.** 어떤 필드가 있어야 하는지는
설계 결정이고 검사로 답할 수 없다.

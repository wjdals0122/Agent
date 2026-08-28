# 예외 발생 집계

`config/exception_policy.yaml` 에 적힌 정책과, `data/interim/docs/` 4,201개 문서(part 4,616개)에 실제로 남은 조치 기록을 대조한다.

정책 15개 / version 1

## 문서 처리 결과

| status | 문서 |
|---|---:|
| `ok` | 4,201 |
| `no_source_xml` | 3 |
| **합계** | **4,204** |

실패 0건. `no_source_xml` 3건은 XML 원문이 없는 pdf+html 문서로, **조용히 빠지지 않고 기록된 결손**이다.

## 규칙별 발생 건수

| id | stage | handle | severity | 발생 | part | 문서 | 상태 |
|---|---|---|---|---:|---:|---:|---|
| `E1_bare_ampersand` | sanitize | count_only | info | 84,167 | 1,709 | 1,448 | 기록됨 |
| `E2_bare_lt` | sanitize | count_only | info | 41,907 | 1,352 | 1,165 | 기록됨 |
| `E3_declared_charset_ignored` | encoding | count_only | info | — | — | — | 코드가 판정 (엔진 밖) |
| `E3_cp949_fallback` | encoding | fallback | warn | — | — | — | 코드가 판정 (엔진 밖) |
| `E3_utf8_bom` | encoding | strip | info | — | — | — | 코드가 판정 (엔진 밖) |
| `E4_library_container` | tree | record_only | error | 29,339 | 3,110 | 2,695 | 기록됨 |
| `E5_not_a_table` | table | demote | info | 846,179 | 3,129 | 2,714 | 기록됨 |
| `E6_unit_caption` | table | attach | info | 318,391 | 2,972 | 2,557 | 기록됨 |
| `E6_footnote_caption` | table | attach | info | 12,055 | 2,087 | 2,083 | 기록됨 |
| `E7_missing_period_date` | parse | record_only | info | 155,048 | 1,509 | 1,116 | 기록됨 |
| `E8_acode_repeat` | parse | record_only | error | 1,609,835 | 1,681 | 1,681 | 기록됨 |
| `SWALLOW_charref_bad` | sanitize | fallback | info | — | — | — | 코드가 판정 (엔진 밖) |
| `SWALLOW_peek_name_failed` | parse | fallback | warn | — | — | — | 코드가 판정 (엔진 밖) |
| `SWALLOW_convert_body_failed` | parse | fallback | error | — | — | — | 코드가 판정 (엔진 밖) |
| `SWALLOW_int_cast` | tree | fallback | info | — | — | — | 코드가 판정 (엔진 밖) |

상태 읽는 법 — 이 셋은 서로 다른 사실이다.

- **기록됨** — 규칙이 돌았고 걸렸다.
- **⚠ 도는데 0건** — 규칙이 돌았는데 한 건도 안 걸렸다. 이 코퍼스에 정말 없거나, 규칙이 틀렸거나. 확인이 필요하다.
- **미연결** — 정책에 있고 규칙도 유효한데 그 stage 를 파이프라인이 아직 안 부른다. 0건이 아니라 **안 재봤다**는 뜻이다.
- **코드가 판정 (엔진 밖)** — 정책 엔진이 regex 로 셀 수 없는 규칙. E3 는 인코딩 층이, E4 는 순회가, E5/E6 는 표 모양과 형제 위치가, E8 은 행렬 좌표가 판정한다. 정책은 **무엇을 어떻게 기록할지**를 정하고, 각 규칙의 `implemented_in:` 이 판정하는 코드를 가리킨다.

지금 파이프라인이 돌리는 stage: `parse`, `sanitize`, `table`

그중 정책 엔진이 정규식으로 직접 도는 stage: `sanitize`. 나머지(`parse`, `table`)는 정규식 한 방으로 셀 수 없어서(표 모양·형제 위치·행렬 좌표) 코드가 판정하고 조치 기록만 같은 규칙 id 로 남긴다 — `encoding` 과 같은 방식이다.

## 문서군별 발생 건수

| id | exchange | major | holding | periodic | 합계 |
|---|---:|---:|---:|---:|---:|
| `E1_bare_ampersand` | 92 | 130 | 1,718 | 82,227 | 84,167 |
| `E2_bare_lt` | 0 | 271 | 250 | 41,386 | 41,907 |
| `E4_library_container` | 0 | 1,005 | 2,025 | 26,309 | 29,339 |
| `E5_not_a_table` | 0 | 2,559 | 9,181 | 834,439 | 846,179 |
| `E6_unit_caption` | 0 | 586 | 2,859 | 314,946 | 318,391 |
| `E6_footnote_caption` | 0 | 33 | 3,034 | 8,988 | 12,055 |
| `E7_missing_period_date` | 9 | 124 | 27 | 154,888 | 155,048 |
| `E8_acode_repeat` | 0 | 63,070 | 1,546,765 | 0 | 1,609,835 |

## 실제 원문 표본

**`E1_bare_ampersand`**

- `&엑스지바 바이오시밀러) 미국 품목허`

**`E2_bare_lt`**

- `< 자본시장과 금융투자업에 관한 법률 시행령 제154조 제1항 각 호>`
- `<삭제>`

## 정책에 적힌 실측치 대조

정책 파일의 `measured:` 는 1단계 census(`scripts/01_exception_census.py`)가 **별도 코드로** 잰 값이다. 아래 "이번 집계"는 doc.json 에 남은 조치 기록에서 나온다. 두 경로가 같은 답을 내야 한다.

| id | 정책 measured | 이번 집계 | 일치 |
|---|---:|---:|---|
| `E1_bare_ampersand` | 84,167 | 84,167 | ✅ |
| `E2_bare_lt` | 41,907 | 41,907 | ✅ |

<sub>생성: `scripts/04_exception_summary.py`</sub>

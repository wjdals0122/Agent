# 임베딩 건강검진

- 실행 시각: 2026-08-30T08:47:05.275758+00:00
- 모델: `BAAI/bge-m3` / dim=1024 / max_length=1024
- 인덱스 행 수: 614,578 · 표본 200건 (seed=0)
- 결과: **전 항목 통과**

## 검사 항목

| 항목 | 결과 | 상세 |
| --- | --- | --- |
| 행 수 일치 (dense / id_map / meta / text_offsets) | PASS | dense=614,578 id_map=614,578 meta=614,578 offsets=614,578 |
| corp_code 결측 0건 | PASS | 0건 |
| doc_group 결측 0건 | PASS | 0건 |
| 표본 chunk_id ↔ 원본 정합 | PASS | 불일치 0건 |
| 표본 embed_sha1 일치 | PASS | 불일치 0건 |
| 셀프 리트리벌 top-1 ≥ 0.95 | PASS | 1.0000 (정확 178/200, 동일텍스트 22건) |
| 자기 코사인 > 0.999 | PASS | min=0.999335 mean=1.000028 |

## 중단 조건 판정

| 조건 | 판정 |
| --- | --- |
| chunks / id_map / dense 행 수 불일치 | 해당 없음 |
| 셀프 리트리벌 top-1 < 0.95 | 해당 없음 |
| corp_code · doc_group 결측 | 해당 없음 |

- 검사 소요: 36.7초

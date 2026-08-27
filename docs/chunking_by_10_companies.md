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

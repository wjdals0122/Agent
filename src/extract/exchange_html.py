# -*- coding: utf-8 -*-
"""거래소공시(exchange) 구조화 — 5단계.

명세는 셀 개수로 판정하라고 했다 — 3개=2단키, 2개=1단키, 1개=섹션헤더
또는 노트 본문. **그 판정은 이미 되어 있다.** exchange 파서가 30,525행을
전수 분류해(미분류 0건) 조각을 이렇게 내놓는다:

    ('h',  레벨, 제목)                     섹션 헤더      ← 1칸 라벨
    ('kv', [키1], 값, [섹션경로])           1단키          ← 셀 2개
    ('kv', [키1, 키2], 값, [섹션경로])      2단키          ← 셀 3개
    ('p',  글자)                           자유 서술      ← 1칸 값
    ('t',  [머리글], [[행]])                정정 비교표

그래서 HTML 을 다시 파싱하지 않는다. **doc.json 의 chunks 에서 만든다** —
XML/HTML 파싱은 문서당 한 번이라는 원칙(3단계)을 지키고, 청킹 전략을
바꿔도 원문을 다시 안 읽는다.

────────────────────────────────────────────────────────────────────────
notes — 원문 그대로 보존
────────────────────────────────────────────────────────────────────────
`9. 기타 투자판단에 참고할 사항` 같은 자유 서술에는 **환산 기준과
기준시점**이 들어 있다 (예: "자기자본은 2022-11-23 최초 공시했던 2021년말
기준임"). 이걸 요약하거나 정규화하면 숫자의 의미가 바뀐다.
그래서 `notes` 에 **원문 문자열 그대로** 넣는다. 손대지 않는다.
"""
import re

__all__ = ['build', 'RE_FREETEXT_KEY']

# '9. 기타 투자판단에 참고할 사항', '기타 투자판단 관련 사항' 등
RE_FREETEXT_KEY = re.compile(r'기타\s*투자판단|참고할\s*사항|기타\s*사항')


def build(doc):
    """exchange doc dict → 구조화 레코드.

    doc 은 파서가 돌려준 것이든 doc.json 에서 읽은 것이든 상관없다
    (chunk 는 tuple 이든 list 든 색인 방식이 같다).
    """
    sections = []
    cur = None
    notes = []
    corrections = []
    n_fields = 0

    def open_section(title):
        nonlocal cur
        cur = {'title': title, 'fields': [], 'notes': []}
        sections.append(cur)
        return cur

    for c in doc.get('chunks') or []:
        kind = c[0]

        if kind == 'h':
            open_section(c[2])
            continue

        if kind == 'kv':
            parts, val = list(c[1]), c[2]
            path = list(c[3]) if len(c) > 3 and c[3] else []
            if cur is None:
                open_section(path[0] if path else None)
            key = ' > '.join(parts)
            rec = {
                'key': key,
                'key_parts': parts,
                'depth': len(parts),          # 1 = 1단키, 2 = 2단키
                'value': val,
                'section_path': path,
            }
            # 자유 서술형 항목은 값이 길고 정규화하면 안 된다.
            if RE_FREETEXT_KEY.search(key):
                rec['freetext'] = True
                notes.append({'key': key, 'text': val,
                              'section_path': path, 'source': 'kv'})
                cur['notes'].append(rec)
            else:
                cur['fields'].append(rec)
                n_fields += 1
            continue

        if kind == 'p':
            text = c[1]
            if cur is None:
                open_section(None)
            # 표 밖 자유 서술. 환산·기준시점이 여기 숨어 있다.
            note = {'key': None, 'text': text,
                    'section_path': [cur['title']] if cur['title'] else [],
                    'source': 'paragraph'}
            notes.append(note)
            cur['notes'].append(note)
            continue

        if kind == 't':
            headers, rows = list(c[1]), [list(r) for r in c[2]]
            tbl = {'headers': headers, 'rows': rows,
                   'section': cur['title'] if cur else None}
            # 정정 비교표는 |항목|정정전|정정후| 모양이다
            if any('정정' in (h or '') for h in headers):
                corrections.append(tbl)
            if cur is not None:
                cur.setdefault('tables', []).append(tbl)
            continue

    sections = [s for s in sections
                if s['fields'] or s['notes'] or s.get('tables')]

    return {
        'kind': 'exchange',
        'disclosure_type': doc.get('공시유형'),
        'is_correction': bool(doc.get('정정공시')),
        'n_sections': len(sections),
        'n_fields': n_fields,
        'sections': sections,
        'notes': notes,                  # 원문 그대로
        'corrections': corrections,
        'parse_confidence': 'high' if n_fields else 'low',
        'confidence_reasons': [] if n_fields else ['키-값 항목 0건'],
    }

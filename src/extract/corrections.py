# -*- coding: utf-8 -*-
"""정정공시 구조화 — 무엇이 무엇으로 바뀌었나.

정정공시는 "이 값이 틀렸다"고 말하는 문서다. 원래 값과 고친 값을
같이 붙들지 못하면, 나중에 어느 쪽을 답해야 하는지 알 수 없다.
그래서 **정정 전/후 쌍**을 따로 뽑는다.

문서군마다 정정을 표시하는 방법이 다르다 (기존 코드에서 확인된 사실).

    exchange   제목 문자열 '정정신고(보고)' 로 판별.
               `div[id^=LIB_]` 존재 여부로 판단하면 안 된다 — 정정 블록과
               부록 블록이 그 상자를 같이 써서 660건이 잡히고 그중 29건이
               잘못 부풀려진다. 제목으로 판별하면 631건 = 비교표 631건과
               정확히 일치한다.
    major/holding/periodic
               `<CORRECTION>` 태그 유무. manifest 의 is_correction 과
               598/598 일치가 확인돼 있다.

정정 비교표는 `| 항목 | 정정사유 | 정정전 | 정정후 |` 또는
`| 정정항목 | 정정전 | 정정후 |` 모양이다. 열 이름이 문서마다 달라서
**위치가 아니라 머리글 글자**로 열을 찾는다.
"""
import re

__all__ = ['find_comparison_tables', 'build', 'RE_BEFORE', 'RE_AFTER']

RE_BEFORE = re.compile(r'정정\s*전')
RE_AFTER = re.compile(r'정정\s*후')
RE_ITEM = re.compile(r'정정\s*항목|항\s*목|구\s*분')
RE_REASON = re.compile(r'정정\s*사유|사\s*유')


def _find_col(headers, rx):
    for i, h in enumerate(headers):
        if h and rx.search(h):
            return i
    return None


def find_comparison_tables(chunks):
    """조각 목록에서 정정 비교표만 골라낸다.

    '정정전'과 '정정후' 열이 **둘 다** 있어야 비교표다. 하나만 있으면
    그냥 정정 사유를 적은 표라 쌍을 만들 수 없다.
    """
    out = []
    for c in chunks or []:
        if c[0] != 't':
            continue
        headers = list(c[1])
        bi = _find_col(headers, RE_BEFORE)
        ai = _find_col(headers, RE_AFTER)
        if bi is None or ai is None:
            continue
        out.append({
            'headers': headers,
            'rows': [list(r) for r in c[2]],
            'col_item': _find_col(headers, RE_ITEM),
            'col_reason': _find_col(headers, RE_REASON),
            'col_before': bi,
            'col_after': ai,
        })
    return out


def build(doc, doc_group):
    """doc dict → 정정 구조. 정정공시가 아니면 None.

    doc.json 의 chunks 에서만 만든다. 원문을 다시 읽지 않는다.
    """
    if not doc.get('정정공시'):
        return None

    tables = find_comparison_tables(doc.get('chunks'))
    pairs = []
    for t in tables:
        for r in t['rows']:
            def cell(i):
                return (r[i] if i is not None and i < len(r) else '') or ''
            before, after = cell(t['col_before']), cell(t['col_after'])
            item = cell(t['col_item'])
            if not (before or after):
                continue
            pairs.append({
                'item': item.strip(),
                'reason': cell(t['col_reason']).strip() or None,
                'before': before,
                'after': after,
                'changed': before.strip() != after.strip(),
            })

    changed = [p for p in pairs if p['changed']]
    reasons = []
    for key in ('3. 정정사유', '정정사유'):
        for c in doc.get('chunks') or []:
            if c[0] == 'kv' and key in ' > '.join(c[1]):
                reasons.append(c[2])
    return {
        'kind': 'correction',
        'doc_group': doc_group,
        'n_tables': len(tables),
        'n_pairs': len(pairs),
        'n_changed': len(changed),
        'reasons': reasons,
        'pairs': pairs,
        # 비교표가 없는 정정공시가 있다. 정정 사실은 맞지만 무엇이
        # 바뀌었는지 이 문서만으로는 알 수 없다 — 확신을 낮춘다.
        'parse_confidence': 'high' if pairs else 'low',
        'confidence_reasons': [] if pairs else ['정정 비교표 없음'],
    }

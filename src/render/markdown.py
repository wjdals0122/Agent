# -*- coding: utf-8 -*-
"""render 층 — 조각(chunk) 목록 → 마크다운. **md 를 아는 유일한 곳.**

파서는 원래부터 마크다운을 만들지 않았다. `('h',레벨,글자)` `('p',글자)`
`('kv',[키조각],값,코드,정규화값)` `('t',[머리글],[[행]])` 네 가지 튜플을
쌓아 `doc['chunks']` 로 넘길 뿐이다. 그래서 render 층은 사실상 이미
분리되어 있었고, 여기서는 **집만 옮긴다.**

DSD 세 파서(major/holding/periodic)의 `to_markdown` 은 대조 결과
**기본 제목 문자열 하나만 다르다** (주요사항보고서 / 대량보유상황보고서 /
사업보고서). 그래서 57줄짜리 함수 세 벌 대신 매개변수 하나로 합쳤다.
주석 한 줄 차이는 출력에 영향이 없다.

3단계 준비
    `render_dsd` 는 `doc` dict 만 읽는다. 트리도, 노드도, 파서도 안 본다.
    그래서 `doc` 을 그대로 JSON 으로 굴려도 동작이 같다 — chunk 는 전부
    str / None / list 라 tuple→list 변환을 이 코드가 구분하지 않는다
    (`c[0]`, `c[1]` 색인과 언패킹만 쓴다). 3단계에서 입력을 doc.json 으로
    바꿀 때 이 파일은 손댈 필요가 없다.
"""
import re

__all__ = ['render_dsd', 'iso_from_aunitvalue']

RE_BLANKS = re.compile(r'\n{3,}')


def iso_from_aunitvalue(norm, re_iso8, re_iso_range):
    """AUNITVALUE 를 사람이 읽을 날짜로. 4가지 형식뿐임을 확인했다.

    원래 각 파서의 `_iso`. 정규식을 인자로 받는 것 말고는 동일하다.
    """
    if not norm:
        return None
    m = re_iso8.match(norm)
    if m:
        return '%s-%s-%s' % m.groups()
    m = re_iso_range.match(norm)
    if m:
        a, b = m.group(1), m.group(2)
        return '%s-%s-%s ~ %s-%s-%s' % (a[:4], a[4:6], a[6:],
                                        b[:4], b[4:6], b[6:])
    return None


def render_dsd(doc, default_title, iso, esc, with_header=True):
    """DSD-XML 문서군(major / holding / periodic) 공통 렌더러.

    doc            : 파서가 돌려준 dict (`chunks` 포함)
    default_title  : `doc['문서종류']` 가 비었을 때 쓸 제목.
                     세 파서의 **유일한 차이**다.
    iso            : norm → 'YYYY-MM-DD' 또는 None
    esc            : 표 셀 이스케이프 (normalize.value.escape_cell)
    """
    lines = []

    if with_header:
        lines.append('# %s' % (doc['문서종류'] or default_title))
        meta = [x for x in [
            doc['회사명'],
            ('접수번호 %s' % doc['접수번호']) if doc['접수번호'] else None,
            '정정공시' if doc['정정공시'] else None,
        ] if x]
        if meta:
            lines.append('> ' + ' · '.join(meta))
        lines.append('')

    for c in doc['chunks']:
        kind = c[0]

        if kind == 'h':
            lines.append('')
            lines.append('#' * min(max(c[1], 1), 6) + ' ' + c[2])
            lines.append('')

        elif kind == 'p':
            lines.append(c[1])
            lines.append('')

        elif kind == 'kv':
            _, parts, value, code, norm = c
            key = ' > '.join(parts)
            if value is None:
                lines.append('- **%s**: _(해당없음)_' % key)
                continue
            iso_v = iso(norm)
            # 화면 글자가 한글 날짜면 뒤에 ISO 날짜를 붙여준다
            tail = ' (%s)' % iso_v if (iso_v and iso_v not in value) else ''
            if '\n' in value:
                seg = value.split('\n')
                lines.append('- **%s**: %s' % (key, seg[0]))
                for s in seg[1:]:
                    lines.append('  ' + s)
                if tail:
                    lines[-1] += tail
            else:
                lines.append('- **%s**: %s%s' % (key, value, tail))

        elif kind == 't':
            headers, rows = c[1], c[2]
            n = len(headers)
            lines.append('')
            lines.append('| ' + ' | '.join(esc(h) for h in headers) + ' |')
            lines.append('| ' + ' | '.join('---' for _ in headers) + ' |')
            for r in rows:
                cells = [esc(x) for x in r] + [''] * (n - len(r))
                lines.append('| ' + ' | '.join(cells[:n]) + ' |')
            lines.append('')

    text = '\n'.join(lines)
    return RE_BLANKS.sub('\n\n', text).strip() + '\n'

# -*- coding: utf-8 -*-
"""E5 — 이 TABLE 이 진짜 표인가. detect 층 (판정만, 부작용 없음).

실측(4,616건): TABLE **1,577,395개 중 846,179개(53.6%)** 가
`rows<=1 or cols<=1`. 절반 이상이 표가 아니다. 그 대부분은
`(단위 : 억원, %)` 같은 캡션 한 칸짜리다(단위 캡션만 318,391건).

기존 코드는 이걸 **md 렌더링 안에서만** 판정한다
(`_one_cell()`: 1칸이면 제목 아니면 문단). 판정 기준이 "셀이 정확히 1개"라
`rows<=1 or cols<=1` 보다 좁고, 결과가 밖으로 안 나와서 구조화 경로가
"이 표는 못 믿는다"를 알 방법이 없다.

여기서는 **판정만** 한다. 아무것도 바꾸지 않는다. 마크다운 경로는 이
모듈을 부르지 않으므로 베이스라인에 영향이 없다.

────────────────────────────────────────────────────────────────────────
E6 — 단위 / 각주 귀속
────────────────────────────────────────────────────────────────────────
실측으로 방향이 확정됐다. 단위 캡션 318,391건 중 **바로 다음 형제만**
진짜 표인 경우가 54.8%, **바로 이전 형제만** 진짜 표인 경우가 0.2%.
→ 단위표는 다음 형제에 귀속. 명세대로다.

각주(`※`)는 다르다. 12,055건 중 앞에만 31.8% / 뒤에만 10.0% 로 방향은
명세대로 이전 형제 쪽이지만, **45.2%는 양옆 어디에도 진짜 표가 없다.**
그냥 떠 있는 주석이다. 그래서 `attach_footnote` 는 옆에 진짜 표가 있을
때만 붙인다 — 무조건 붙이면 없는 귀속을 만들어낸다.
"""
import re

__all__ = ['shape', 'classify', 'is_real_table',
           'caption_kind', 'attach_direction', 'patterns']

# 정규식은 여기에 박아 두지 않는다. config/exception_policy.yaml 이
# 사실의 출처다 — 코드와 정책 두 군데에 같은 패턴이 있으면 한쪽만 고치고
# 왜 안 바뀌냐고 헤매게 된다. (같은 이유로 normalize/sanitize.py 는 정책
# 엔진으로 대체하고 지웠다.)
_PAT = {}


def patterns(pol=None):
    """정책에서 캡션 정규식을 꺼내 컴파일한다. 한 번만."""
    if _PAT:
        return _PAT
    if pol is None:
        from normalize import policy as policy_mod
        pol = policy_mod.load()
    for rid, key in (('E6_unit_caption', 'unit'),
                     ('E6_footnote_caption', 'footnote')):
        rule = pol.by_id.get(rid)
        if rule is not None and rule.detect.get('pattern'):
            _PAT[key] = re.compile(rule.detect['pattern'])
    return _PAT

REAL = 'real_table'          # 행·열 둘 다 2 이상
DEGENERATE = 'not_a_table'   # rows<=1 or cols<=1
EMPTY = 'empty'


def shape(rows):
    """rows: 행마다 셀 리스트. (nrows, ncols) 를 준다. 빈 행은 뺀다."""
    rows = [r for r in rows if r]
    if not rows:
        return 0, 0
    return len(rows), max(len(r) for r in rows)


def classify(rows):
    """부작용 없는 판정. 진단 dict."""
    nr, nc = shape(rows)
    if nr == 0 or nc == 0:
        kind = EMPTY
    elif nr <= 1 or nc <= 1:
        kind = DEGENERATE
    else:
        kind = REAL
    return {'kind': kind, 'rows': nr, 'cols': nc,
            'rule': 'E5_not_a_table' if kind == DEGENERATE else None}


def is_real_table(rows):
    return classify(rows)['kind'] == REAL


def caption_kind(text, pol=None):
    """1칸 표의 글자가 단위인지 각주인지 그냥 문단인지.

    판정 기준은 config/exception_policy.yaml 의 E6_* 규칙에서 온다.
    """
    pats = patterns(pol)
    t = text or ''
    if 'unit' in pats and pats['unit'].search(t):
        return 'unit'
    if 'footnote' in pats and pats['footnote'].match(t):
        return 'footnote'
    return None


def attach_direction(kind, prev_is_real, next_is_real, pol=None):
    """캡션을 어느 쪽 표에 붙일지. 붙일 데가 없으면 None.

    kind          : caption_kind() 결과
    prev_is_real  : 바로 이전 형제 TABLE 이 진짜 표인가
    next_is_real  : 바로 다음 형제 TABLE 이 진짜 표인가

    실측 근거는 이 파일 맨 위 주석 참조. 핵심은 **없으면 안 붙인다**는 것.
    각주의 45.2%가 여기 해당한다.
    """
    if kind == 'unit':
        if next_is_real:
            return 'next'
        if prev_is_real:
            return 'prev'      # 0.2%. 드물지만 있다.
        return None
    if kind == 'footnote':
        if prev_is_real:
            return 'prev'
        if next_is_real:
            return 'next'
        return None            # 45.2% — 표의 각주가 아니라 그냥 주석
    return None

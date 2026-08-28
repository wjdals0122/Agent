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
           'caption_kind', 'attach_direction', 'patterns',
           'scan', 'unit_of']

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


# ══════════════════════════════════════════════════════════════════════
# table stage — 문서 한 건을 훑어 E5/E6 를 판정한다 (부작용 없음)
# ══════════════════════════════════════════════════════════════════════
#
# 여기까지가 "판정만" 이라는 이 파일의 계약 안이다. scan() 은 트리를
# 읽기만 하고 아무것도 바꾸지 않는다. 무엇을 어디에 붙일지 **정하기만**
# 하고, 실제로 그 결정을 쓰는 것은 doc.json 을 읽는 쪽이다.
#
# 표 색인(table_idx)은 tree.walk 순서, 즉 **중첩 표를 포함한** 문서 순서다.
# extract/acode.py 의 table_idx 는 바깥 표만 세므로 **다른 색인 공간**이다.
# 두 색인을 서로 조인하지 마라.
#
# 1단계 census(scripts/01_exception_census.py `_scan_tree`)와 같은
# 판정법을 쓴다 — 같은 코퍼스에서 같은 수가 나와야 정책의 measured 와
# 대조할 수 있다. 캡션 판정은 '진짜 표가 아닌 표'에만 적용하고,
# 이웃은 "문서 어딘가"가 아니라 **바로 옆 형제 TABLE** 이다.

_FOOT_TEXT_MAX = 300      # 각주 원문은 붙일 값이므로 남긴다 (자르되)


def _shape_of(table, tree):
    trs = [e for e in tree.own_nodes(table) if e.tag == 'TR']
    rows = [r for r in ([c for c in tr.children if c.tag in tree.CELL_TAGS]
                        for tr in trs) if r]
    if not rows:
        return None
    return len(rows), max(len(r) for r in rows)


def _sibling_tables(node, shapes):
    """형제 순서에서 바로 앞/뒤의 TABLE. 없으면 None."""
    sibs = [c for c in (node.parent.children if node.parent else [])
            if c.tag == 'TABLE' and id(c) in shapes]
    try:
        i = sibs.index(node)
    except ValueError:
        return None, None
    return (sibs[i - 1] if i > 0 else None,
            sibs[i + 1] if i + 1 < len(sibs) else None)


def unit_of(text):
    """'(단위 : 천원)' → '천원'. 못 찾으면 None."""
    import re as _re
    m = _re.search(r'\(\s*단\s*위\s*[:：]\s*([^)]*)\)', text or '')
    return m.group(1).strip() if m else None


def scan(root, tree, value, pol=None):
    """문서 트리 → E5/E6 판정 결과. 아무것도 바꾸지 않는다.

    돌려주는 dict

        n_tables / n_not_a_table      E5
        unit / footnote               캡션 수와 이웃 분포 (census 와 같은 축)
        attach                        **붙일 곳이 정해진 것만** 들어간다.
                                      붙일 데가 없으면 여기 없다 —
                                      각주의 45.2%가 그 경우다.

    attach 한 항목
        i     캡션 표의 table_idx
        to    붙일 표의 table_idx
        dir   'next' | 'prev'
        kind  'unit' | 'footnote'
        unit  단위 문자열 (kind='unit')
        text  각주 원문 (kind='footnote', 잘림)
    """
    tables = [n for n in tree.walk(root) if n.tag == 'TABLE']
    shapes = {}
    idx = {}
    for i, t in enumerate(tables):
        s = _shape_of(t, tree)
        if s is None:
            continue                       # 행도 칸도 없는 것은 세지 않는다
        shapes[id(t)] = s
        idx[id(t)] = i

    n_tab = len(shapes)
    n_deg = sum(1 for s in shapes.values() if s[0] <= 1 or s[1] <= 1)

    def real(node):
        if node is None or id(node) not in shapes:
            return False
        r, c = shapes[id(node)]
        return r > 1 and c > 1

    counts = {'unit': dict(total=0, next_only=0, prev_only=0, both=0,
                           neither=0, attached=0),
              'footnote': dict(total=0, next_only=0, prev_only=0, both=0,
                               neither=0, attached=0)}
    attach = []

    for t in tables:
        if id(t) not in shapes:
            continue
        r, c = shapes[id(t)]
        if r > 1 and c > 1:
            continue                       # 진짜 표는 캡션이 아니다
        txt = value.flat(tree.text(t))
        kind = caption_kind(txt, pol)
        if kind is None:
            continue
        prev, nxt = _sibling_tables(t, shapes)
        pr, nx = real(prev), real(nxt)
        cnt = counts[kind]
        cnt['total'] += 1
        if pr and nx:
            cnt['both'] += 1
        elif nx:
            cnt['next_only'] += 1
        elif pr:
            cnt['prev_only'] += 1
        else:
            cnt['neither'] += 1

        d = attach_direction(kind, pr, nx, pol)
        if d is None:
            continue                       # 붙일 데가 없다 — 만들어내지 않는다
        target = nxt if d == 'next' else prev
        cnt['attached'] += 1
        rec = {'i': idx[id(t)], 'to': idx[id(target)], 'dir': d, 'kind': kind}
        if kind == 'unit':
            rec['unit'] = unit_of(txt)
        else:
            rec['text'] = txt[:_FOOT_TEXT_MAX]
        attach.append(rec)

    return {'n_tables': n_tab, 'n_not_a_table': n_deg,
            'unit': counts['unit'], 'footnote': counts['footnote'],
            'attach': attach}

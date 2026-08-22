# -*- coding: utf-8 -*-
"""{XBRL} 재무제표 구조화 — 5단계.

대상은 `TABLE-GROUP[@ACLASS]` 가 `{XBRL}` 로 시작하고 **주석(NT_*)이 아닌**
것만이다. 정기공시 주석표 509종 44,037개는 건드리지 않는다 — 명세대로
md 청크로 흘려보내고 parse_confidence 를 붙인다.

────────────────────────────────────────────────────────────────────────
실측이 명세를 뒤집은 두 번째 지점 ★
────────────────────────────────────────────────────────────────────────
명세는 `{XBRL}BS|IS2|IS3|EF|CF` 에 "`_S` 접미사는 별도재무제표"라고 했다.
코퍼스를 전수로 재 보니 XBRL 비주석 ACLASS 는 **18종 3계열**이었다.

    _S 별도    BS_S IS_S1 IS_S2 IS_S3 CF_S EF_S     1,020 문서
    _C 연결    BS_C IS_C1 IS_C2 IS_C3 CF_C EF_C       892 문서
    접미사 없음 BS   IS1   IS2   IS3   CF   EF         119 문서

명세의 패턴은 **접미사 없는 계열(119문서)에만** 맞는다. 그대로 구현하면
별도 1,020 + 연결 892 문서의 재무제표를 통째로 놓친다(약 94% 유실).
게다가 명세는 `_C`(연결)를 아예 언급하지 않는데, 연결이야말로 지주·
그룹사에서 더 중요한 쪽이다.

그래서 계열을 접미사로 판별한다. 접미사가 붙든 안 붙든 같은 재무제표다.

────────────────────────────────────────────────────────────────────────
TABLE-GROUP 안의 모양 (실측)
────────────────────────────────────────────────────────────────────────
    TABLE-GROUP[@ACLASS="{XBRL}BS_S"]
      TABLE#0  캡션표 (열 1개)   재무상태표
                                 제 17 기 1분기말 2023.03.31 현재
                                 제 16 기말      2022.12.31 현재
                                 (단위 : 천원)
      TABLE#1  데이터표          | 제 17 기 1분기말 | 제 16 기말 |
                                 | 자산 …          | 2,227,819,930 | …

E7(회계기간 부재)의 답이 여기 있다 — 데이터표 헤더는 `제 17 기 1분기말`
까지만 적혀 있고 **실제 날짜는 캡션표에 있다.** 기수 문자열로 이어 붙인다.

E6(단위 귀속)도 여기서 확인된다 — `(단위 : 천원)` 이 데이터표 **앞**에
온다. 실측에서 단위 캡션의 54.8%가 "다음 형제만 진짜 표"였던 것과 맞는다.
"""
import re

__all__ = ['XBRL_PREFIX', 'is_target_aclass', 'parse_aclass',
           'extract_groups', 'STATEMENTS']

XBRL_PREFIX = '{XBRL}'

STATEMENTS = {
    'BS': '재무상태표',
    'IS': '손익계산서',
    'CF': '현금흐름표',
    'EF': '자본변동표',
}

# 접미사 없는 계열은 '미상'이 아니라 **연결**이다. 두 가지로 확인했다.
#   1. 동시 출현: bare 와 _C 가 같이 나오는 문서는 **0건**이다.
#      (bare+_S 119문서 / _C+_S 892문서 / _S만 9문서 / 없음 446문서)
#      즉 bare 와 _C 는 같은 자리를 쓰는 서식 판본 차이다.
#   2. 문서 자신의 캡션: {XBRL}BS 의 첫 줄이 "연결 재무상태표",
#      {XBRL}BS_S 는 "재무상태표"(별도).
# 'unspecified' 로 두면 119문서의 연결재무제표가 나머지 892문서와
# 같은 팩트 테이블에서 안 묶인다.
BASIS = {'S': 'separate', 'C': 'consolidated', '': 'consolidated'}
BASIS_SOURCE = {'S': 'suffix', 'C': 'suffix', '': 'inferred_no_suffix'}

# {XBRL}BS_S / {XBRL}IS_C3 / {XBRL}BS / {XBRL}IS1
_RE_ACLASS = re.compile(r'^\{XBRL\}(BS|IS|CF|EF)(?:_([SC]))?(\d)?$')

# "제 17 기 1분기말 2023.03.31 현재" / "제 16 기 2022.01.01 ~ 2022.12.31"
# 라벨은 '제 N 기' 로 시작해 **첫 날짜 앞까지**다. 접미사에 숫자가 들어가는
# 경우(1분기말, 3분기)가 많아서 [^\d] 로 끊으면 '제 17 기' 로 잘린다.
_RE_PERIOD_START = re.compile(r'제\s*\d{1,3}\s*기')
_RE_DATE = re.compile(r'(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})')
_RE_NUM = re.compile(r'^-?[\d,]+(?:\.\d+)?$')


def parse_aclass(aclass):
    """'{XBRL}BS_S' → dict. 대상이 아니면 None.

    주석(NT_*)은 여기서 걸러진다 — 정규식에 안 맞는다.
    """
    if not aclass:
        return None
    m = _RE_ACLASS.match(aclass.strip())
    if not m:
        return None
    stmt, basis, variant = m.group(1), m.group(2) or '', m.group(3)
    return {
        'aclass': aclass,
        'statement': stmt,
        'statement_name': STATEMENTS[stmt],
        'basis': BASIS[basis],
        'basis_source': BASIS_SOURCE[basis],
        'variant': int(variant) if variant else None,
    }


def is_target_aclass(aclass):
    return parse_aclass(aclass) is not None


def _cells(tree, tr):
    return [c for c in tr.children if c.tag in tree.CELL_TAGS]


def _rows_of(tree, table):
    trs = [e for e in tree.own_nodes(table) if e.tag == 'TR']
    return [r for r in (_cells(tree, tr) for tr in trs) if r]


def _cell_text(tree, value, cell):
    return value.flat(value.clean(tree.text(cell)))


def extract_periods(caption_lines):
    """캡션 줄에서 (기수 라벨 → 날짜) 를 뽑는다. E7 의 답.

    '제 17 기 1분기말 2023.03.31 현재' → label '제 17 기 1분기말',
    date '2023-03-31'. 기간형('~')이면 start/end 둘 다 잡는다.
    """
    out = []
    for line in caption_lines:
        m = _RE_PERIOD_START.search(line)
        if not m:
            continue
        d0 = _RE_DATE.search(line, m.start())
        label = line[m.start():d0.start()] if d0 else line[m.start():]
        label = re.sub(r'\s+', ' ', label).strip()
        dates = [_iso(d) for d in _RE_DATE.findall(line)]
        rec = {'label': label, 'raw': line.strip()}
        if len(dates) >= 2:
            rec.update(kind='duration', start=dates[0], end=dates[1])
        elif len(dates) == 1:
            rec.update(kind='instant', date=dates[0])
        else:
            rec.update(kind='unknown')
        out.append(rec)
    return out


def _iso(t):
    y, mo, d = t
    return '%04d-%02d-%02d' % (int(y), int(mo), int(d))


def extract_groups(root, tree, value, policy=None, grid_mod=None,
                   cell_cls=None, router=None):
    """문서 트리에서 {XBRL} 재무제표 그룹을 전부 뽑는다.

    root      : 파서가 세운 트리의 루트
    tree      : normalize.tree 모듈
    value     : normalize.value 모듈
    grid_mod  : normalize.grid (rowspan/colspan 펼치기)
    cell_cls  : 그 문서군 파서의 _Cell 클래스
    router    : normalize.table_router (단위/각주 판정)

    모듈을 인자로 받는 이유는 문서군마다 _Cell 이 다르기 때문이다
    (major 와 holding+periodic 이 서로 다르다 — 2단계에서 확인).
    """
    groups = []
    for node in tree.walk(root):
        if node.tag != 'TABLE-GROUP':
            continue
        info = parse_aclass(node.attrs.get('ACLASS'))
        if info is None:
            continue

        tables = [t for t in tree.own_tables(node)]
        caption_lines = []
        unit = None
        data_table = None

        for t in tables:
            rows = _rows_of(tree, t)
            if not rows:
                continue
            ncol = max(len(r) for r in rows)
            if ncol <= 1:
                # 캡션표 — 제목·기수·단위가 여기 있다 (E5 의 '표 아님')
                for r in rows:
                    txt = _cell_text(tree, value, r[0])
                    if not txt:
                        continue
                    caption_lines.append(txt)
                    if router is not None and unit is None:
                        if router.caption_kind(txt, policy) == 'unit':
                            unit = _unit_of(txt)
            else:
                data_table = t          # 마지막 다열 표가 데이터표다

        if data_table is None:
            groups.append(dict(info, parse_confidence='low',
                               reason='데이터표 없음',
                               caption_lines=caption_lines, unit=unit,
                               periods=extract_periods(caption_lines),
                               header=[], rows=[]))
            continue

        raw = _rows_of(tree, data_table)
        g, ncol = grid_mod.expand(raw, cell_cls)
        header, body, hsrc = _split_header(tree, value, g, ncol)
        periods = extract_periods(caption_lines)

        rec = dict(info)
        rec.update(
            caption_lines=caption_lines,
            unit=unit,
            periods=periods,
            header=header,
            header_source=hsrc,
            rows=body,
            n_rows=len(body),
            n_cols=ncol,
        )
        rec['columns'] = _map_columns(header, periods)
        groups.append(rec)
    return groups


def _unit_of(text):
    """'(단위 : 천원)' → '천원'."""
    m = re.search(r'\(\s*단\s*위\s*[:：]\s*([^)]*)\)', text)
    return m.group(1).strip() if m else None


def _split_header(tree, value, g, ncol):
    """헤더 밴드와 본문을 가른다. 본체는 normalize/header.py.

    판정 로직은 재무제표 전용이 아니라서 밖으로 뺐다. 여기서는 밴드를
    받아 본문만 추려 준다.
    """
    from normalize import header as H
    hdr, start, source = H.detect_band(g)
    body = [H.row_texts(g[r]) for r in range(start, len(g))]
    body = [b for b in body if any(x.strip() for x in b)]
    return hdr, body, source


def _map_columns(header, periods):
    """데이터표 헤더의 기수 라벨을 캡션표의 날짜에 잇는다. E7 의 마무리.

    정확히 같지 않다. 실측 두 모양:

      재무상태표  헤더 '제 17 기 1분기말'      캡션 '제 17 기 1분기말' → 일치
      손익계산서  헤더 '제 17 기 1분기 3개월'  캡션 '제 17 기 1분기'   → 접두

    손익계산서는 같은 기간을 3개월/누적 두 열로 쪼갠다. 그래서 **가장 긴
    접두사**로 잇고, 남는 꼬리는 측정 구분(qualifier)으로 따로 남긴다.
    꼬리를 버리면 3개월과 누적이 구분되지 않아 값이 뒤섞인다.
    """
    norm = lambda s: re.sub(r'\s+', '', s or '')
    # 긴 라벨부터 봐야 '제 17 기' 가 '제 17 기 1분기' 를 가로채지 않는다
    ranked = sorted(periods, key=lambda p: -len(norm(p['label'])))
    cols = []
    for i, h in enumerate(header):
        key = norm(h)
        col = {'index': i, 'header': h}
        for p in ranked:
            lab = norm(p['label'])
            if lab and key.startswith(lab):
                col['period'] = {k: v for k, v in p.items() if k != 'raw'}
                tail = key[len(lab):]
                if tail:
                    col['qualifier'] = tail
                break
        cols.append(col)
    return cols


# ══════════════════════════════════════════════════════════════════════
# 열 축 판정
# ══════════════════════════════════════════════════════════════════════

# 자본변동표는 열이 기간이 아니라 **자본 구성요소**다.
# (자본금 / 자본잉여금 / 기타자본구성요소 / 이익잉여금 / 비지배지분 …)
# 기간은 행 쪽에 온다. 그래서 열-기간 매핑이 안 되는 게 정상이다.
COLUMN_AXIS = {'BS': 'period', 'IS': 'period', 'CF': 'period',
               'EF': 'equity_component'}


# ══════════════════════════════════════════════════════════════════════
# 정합 검사 — 검증 골든셋 6번 `sums`
# ══════════════════════════════════════════════════════════════════════
#
# 처음에는 BS 만 검사했다. 그 결과 {XBRL} 그룹 8,728개 중 1,934개(22%)만
# 검사를 받고, IS/CF/EF 6,697개는 숫자를 뽑아 놓고 맞는지 확인하는 규칙이
# 하나도 없었다. 그런데도 parse_confidence 에는 'high' 가 붙었다 —
# "검사를 통과했다"가 아니라 "검사할 규칙이 없었다"인데 구분이 안 됐다.
# 그래서 세 가지를 고쳤다.
#
#   1. 항등식을 **부호 있는 선형식**으로 일반화했다. BS 는 덧셈뿐이지만
#      IS 는 뺄셈이다(매출총이익 = 수익 − 매출원가). 덧셈만 되는 틀로는
#      손익계산서를 영영 검사할 수 없다.
#   2. EF(자본변동표)는 열이 기간이 아니라 자본 구성요소라 행 방향으로
#      검사한다 — 합계 열 = 나머지 구성요소 열의 합.
#   3. 검사를 한 건도 못 받은 그룹은 'high' 를 주지 않는다.
#      `unverified` 로 남긴다. 검사 없음과 검사 통과는 다른 사실이다.

_RE_ONLY_NUM = re.compile(r'^-?[\d,]+(?:\.\d+)?$')


def to_number(s):
    """'2,227,819,930' → 2227819930. 숫자가 아니면 None.

    괄호 음수 '(1,234)' 도 받는다 — 재무제표에서 음수 표기의 기본이다.
    """
    if s is None:
        return None
    t = str(s).strip().replace(' ', '')
    if not t:
        return None
    neg = t.startswith('(') and t.endswith(')')
    if neg:
        t = t[1:-1]
    if not _RE_ONLY_NUM.match(t):
        return None
    try:
        v = float(t.replace(',', ''))
    except ValueError:
        return None
    v = -v if neg else v
    return int(v) if v == int(v) else v


def _norm_label(s):
    return re.sub(r'\s+', '', s or '')


# 항등식: (이름, 좌변 라벨 후보들, [(부호, 라벨 후보들), …], [선택 항목])
#
# 라벨은 서식마다 조금씩 다르다(수익(매출액) / 매출액 / 영업수익). 그래서
# 각 항을 **후보 목록**으로 둔다. 하나도 못 찾으면 그 항등식은 건너뛴다 —
# 없는 걸 실패로 세지 않는다.
LINEAR_CHECKS = {
    'BS': [
        ('자산총계', ['자산총계'],
         [(1, ['유동자산']), (1, ['비유동자산'])],
         ['매각예정으로분류된처분집단의자산', '매각예정비유동자산',
          '매각예정으로분류된자산']),
        ('부채총계', ['부채총계'],
         [(1, ['유동부채']), (1, ['비유동부채'])],
         ['매각예정으로분류된처분집단에포함된부채', '매각예정부채']),
        ('자본총계', ['자본총계'],
         [(1, ['지배기업의소유주에게귀속되는자본', '지배기업소유주지분']),
          (1, ['비지배지분'])], []),
        ('자본과부채총계', ['자본과부채총계', '부채와자본총계', '부채및자본총계'],
         [(1, ['부채총계']), (1, ['자본총계'])], []),
    ],
    'IS': [
        ('매출총이익', ['매출총이익'],
         [(1, ['수익(매출액)', '매출액', '영업수익', '수익']),
          (-1, ['매출원가'])], []),
        ('영업이익', ['영업이익', '영업이익(손실)'],
         [(1, ['매출총이익']), (-1, ['판매비와관리비'])], []),
        ('당기순이익', ['당기순이익(손실)', '당기순이익', '분기순이익(손실)',
                        '반기순이익(손실)'],
         [(1, ['법인세비용차감전순이익(손실)', '법인세비용차감전순이익',
                '법인세차감전순이익(손실)']),
          (-1, ['법인세비용'])], []),
        ('순이익 귀속', ['당기순이익(손실)', '당기순이익', '분기순이익(손실)',
                         '반기순이익(손실)'],
         [(1, ['지배기업의소유주지분', '지배기업소유주지분',
                '지배기업의소유주에게귀속되는당기순이익(손실)']),
          (1, ['비지배지분'])], []),
        ('총포괄손익', ['총포괄손익'],
         [(1, ['당기순이익(손실)', '당기순이익', '분기순이익(손실)',
                '반기순이익(손실)']),
          (1, ['기타포괄손익'])], []),
    ],
    'CF': [
        ('환율변동전 순증감', ['환율변동효과반영전현금및현금성자산의순증가(감소)'],
         [(1, ['영업활동현금흐름']), (1, ['투자활동현금흐름']),
          (1, ['재무활동현금흐름'])], []),
        ('현금 순증감', ['현금및현금성자산의순증가(감소)'],
         [(1, ['환율변동효과반영전현금및현금성자산의순증가(감소)']),
          (1, ['현금및현금성자산에대한환율변동효과'])], []),
        ('기말현금', ['기말현금및현금성자산'],
         [(1, ['기초현금및현금성자산']),
          (1, ['현금및현금성자산의순증가(감소)'])], []),
    ],
}


def _pick(by_label, candidates):
    for c in candidates:
        r = by_label.get(_norm_label(c))
        if r is not None:
            return c, r
    return None, None


def _linear_checks(group, tolerance=1):
    """라벨 기반 선형 항등식. BS / IS / CF."""
    specs = LINEAR_CHECKS.get(group.get('statement')) or []
    rows = group.get('rows') or []
    by_label = {}
    for r in rows:
        if r:
            by_label.setdefault(_norm_label(r[0]), r)

    results, bad = [], 0
    ncol = group.get('n_cols') or 0
    for name, total_names, terms, optional in specs:
        tname, trow = _pick(by_label, total_names)
        if trow is None:
            continue
        picked = []
        for sign, cands in terms:
            lab, row = _pick(by_label, cands)
            if row is None:
                picked = None
                break
            picked.append((sign, lab, row))
        if not picked:
            continue
        extra = [(o, by_label[_norm_label(o)]) for o in optional
                 if _norm_label(o) in by_label]

        for ci in range(1, ncol):
            tv = to_number(trow[ci] if ci < len(trow) else None)
            if tv is None:
                continue
            vals = []
            ok_terms = True
            for sign, lab, row in picked:
                v = to_number(row[ci] if ci < len(row) else None)
                if v is None:
                    ok_terms = False
                    break
                vals.append(sign * v)
            if not ok_terms:
                continue
            evs = [to_number(er[ci] if ci < len(er) else None)
                   for _, er in extra]
            evs = [v for v in evs if v is not None]

            # 선택 항목은 더해야 맞는 문서와 더하면 이중계상인 문서가
            # 둘 다 있다(같은 '매각예정…' 라벨이 최상위이기도, 유동자산
            # 하위이기도 하다). 이 검사의 목적은 K-IFRS 분류가 아니라
            # 파싱 오류 탐지이므로, 두 읽기 중 하나만 맞으면 정상으로 본다.
            base = sum(vals)
            cands = [('required_only', base)]
            if evs:
                cands.append(('with_optional', base + sum(evs)))
            hit = next((c for c in cands if abs(c[1] - tv) <= tolerance), None)
            ok = hit is not None
            got = hit[1] if hit else cands[-1][1]
            if not ok:
                bad += 1
            results.append({
                'statement': group.get('statement'),
                'check': '%s: %s = %s' % (
                    name, tname,
                    ' '.join(('+ ' if s > 0 else '- ') + l
                             for s, l, _ in picked).lstrip('+ ')),
                'column': ci, 'total': tv, 'sum': got,
                'diff': got - tv, 'ok': ok,
                'reading': hit[0] if hit else None,
            })
    return results, bad


def _equity_column_checks(group, tolerance=1):
    """자본변동표 — 합계 열 = 나머지 구성요소 열의 합 (행 방향).

    EF 는 열이 기간이 아니라 자본 구성요소라 라벨 항등식이 안 통한다.
    대신 헤더에 '합계' 가 붙은 열이 그 앞의 구성요소 열들의 합이어야 한다.
    실측 구조:
        col1..4  자본금 / 자본잉여금 / 기타자본구성요소 / 이익잉여금
        col5     지배기업의 소유주에게 귀속되는 자본 합계   = col1..4
        col6     비지배지분
        col7     자본 합계                                 = col5 + col6
    """
    if group.get('statement') != 'EF':
        return [], 0
    header = group.get('header') or []
    rows = group.get('rows') or []
    if not header or not rows:
        return [], 0

    owner_total = None      # 지배기업 소유주 귀속 합계 열
    grand_total = None      # 자본 합계 열
    for i, h in enumerate(header):
        n = _norm_label(h)
        if not n.endswith('합계'):
            continue
        if '비지배' in n:
            continue
        if '지배기업의소유주' in n:
            owner_total = i
        else:
            grand_total = i
    nonctrl = next((i for i, h in enumerate(header)
                    if _norm_label(h).endswith('비지배지분')), None)

    results, bad = [], 0

    def run(target, parts, name):
        nonlocal bad
        if target is None or not parts:
            return
        for r in rows:
            tv = to_number(r[target] if target < len(r) else None)
            if tv is None:
                continue
            vs = [to_number(r[p] if p < len(r) else None) for p in parts]
            if any(v is None for v in vs):
                continue
            got = sum(vs)
            ok = abs(got - tv) <= tolerance
            if not ok:
                bad += 1
            results.append({
                'statement': 'EF', 'check': name,
                'row_label': (r[0] or '')[:40], 'column': target,
                'total': tv, 'sum': got, 'diff': got - tv, 'ok': ok,
            })

    if owner_total is not None:
        run(owner_total, [i for i in range(1, owner_total)], '지배기업 귀속 합계 = 구성요소 합')
    if grand_total is not None and owner_total is not None and nonctrl is not None:
        run(grand_total, [owner_total, nonctrl], '자본 합계 = 지배 + 비지배')
    return results, bad


def check_sums(group, tolerance=1):
    """(결과목록, 불일치수). BS/IS/CF 는 선형 항등식, EF 는 열 합계."""
    a, ba = _linear_checks(group, tolerance)
    b, bb = _equity_column_checks(group, tolerance)
    return a + b, ba + bb


def finalize(group):
    """정합을 확인하고 parse_confidence 를 매긴다.

    low   불일치가 있다 / 데이터표가 없다 / 기간 매핑이 하나도 안 됐다
    unverified  검사를 한 건도 못 받았다 — **통과가 아니다**
    high  검사를 받았고 전부 맞았다

    'unverified' 를 따로 둔 이유: 검사할 규칙이 없어서 아무 일도 안 일어난
    것을 'high' 로 부르면, 확인된 숫자와 확인 안 된 숫자가 같은 등급이 된다.
    facts_* 진입 판정은 이 등급을 보고 하므로 구분이 필요하다.
    """
    stmt = group.get('statement')
    axis = COLUMN_AXIS.get(stmt, 'period')
    group['column_axis'] = axis

    sums, bad = check_sums(group)
    group['sum_checks'] = sums
    group['sum_mismatches'] = bad
    group['sum_checked'] = len(sums)

    reasons = []
    if group.get('parse_confidence') == 'low':
        reasons.append(group.get('reason') or '데이터표 없음')
    if bad:
        reasons.append('정합 불일치 %d건' % bad)
    if axis == 'period':
        cols = [c for c in (group.get('columns') or []) if c.get('header')]
        if cols and not any('period' in c for c in cols):
            reasons.append('기간 매핑 0열')
    if not group.get('rows'):
        reasons.append('행 없음')

    if reasons:
        group['parse_confidence'] = 'low'
    elif not sums:
        group['parse_confidence'] = 'unverified'
        reasons.append('적용 가능한 정합 규칙 없음')
    else:
        group['parse_confidence'] = 'high'
    group['confidence_reasons'] = reasons
    return group

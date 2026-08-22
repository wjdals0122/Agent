# -*- coding: utf-8 -*-
"""ACODE 기반 수시공시 구조화 — 5단계.

대상은 major(주요사항보고서)·holding(대량보유상황보고서)의 `TE`/`TU` 셀.
정기공시는 여기 대상이 아니다 (명세).

DART 서식은 값 셀에 `ACODE`(필드코드)를, 단위 셀에 `AUNITVALUE`(정규화값)를
달아 준다. 화면 글자가 '2023년 12월 31일' 이어도 `AUNITVALUE` 는 '20231231'
이라, 사람이 읽는 글자와 기계가 쓰는 값이 둘 다 남는다.

────────────────────────────────────────────────────────────────────────
키 설계 — 실측이 명세를 뒤집은 지점 ★
────────────────────────────────────────────────────────────────────────
명세는 "키는 `(table_idx, row_idx, acode)`. dict 금지" 라고 했다.
`{acode: value}` dict 가 위험하다는 판단은 맞다. 그런데 **명세가 제시한
3튜플 키도 안전하지 않다.** ACODE 를 가진 TE 7,635,332개 기준 실측:

    {acode: value}                          86.75% 유실
    (table_idx, row_idx, acode)             27.45% 유실   ← 명세의 키
    (table_idx, row_idx, col_idx, acode)     0.00% 유실

원인은 **같은 행 안에서 같은 ACODE 가 여러 열에 반복**되는 구조다
(당기/전기, 지배/비지배 같은 다열 표). 실측 2,095,725건.
그래서 열 번호를 키에 넣는다. "dict 금지"라는 명세의 의도는 그대로
지키되 키를 한 칸 넓힌 것이다.

레코드는 **리스트**로 쌓는다. dict 로 모으지 않는다 — 키가 겹치면 조용히
덮어쓰기 때문이다. 리스트면 겹쳐도 둘 다 남고, 겹쳤다는 사실이 보인다.
"""

__all__ = ['extract_facts', 'group_by_row']


def _cells(tree, tr):
    return [c for c in tr.children if c.tag in tree.CELL_TAGS]


def extract_facts(root, tree, value, grid_mod=None, cell_cls=None):
    """트리 → 팩트 리스트.

    각 레코드
        table_idx / row_idx / col_idx   위치 (키의 4요소 중 셋)
        acode                           DART 필드코드 (넷째)
        value                           화면 글자
        norm                            AUNITVALUE (정규화값, 있으면)
        tag                             TE / TU
        labels                          같은 행에서 이 값 왼쪽의 라벨들
        table_path                      이 표까지의 제목 경로

    `TR` 단위로 묶는다 — 라벨은 값의 왼쪽에 있고, 행을 벗어나면 다른
    항목이다.
    """
    facts = []
    path = []
    for ti, table in enumerate(_tables_with_path(root, tree, value, path)):
        tbl, tpath = table
        trs = [e for e in tree.own_nodes(tbl) if e.tag == 'TR']
        for ri, tr in enumerate(trs):
            cs = _cells(tree, tr)
            if not cs:
                continue
            labels = []
            for ci, c in enumerate(cs):
                txt = value.flat(tree.text(c))
                if c.tag in ('TD', 'TH'):
                    # 값의 왼쪽에 쌓인 라벨. 값이 나오면 그 시점의 사본을 쓴다.
                    if txt:
                        labels.append(txt)
                    continue
                code = c.attrs.get('ACODE') or c.attrs.get('AUNIT')
                if not code:
                    continue
                facts.append({
                    'table_idx': ti,
                    'row_idx': ri,
                    'col_idx': ci,
                    'acode': code,
                    'tag': c.tag,
                    'value': txt,
                    'norm': c.attrs.get('AUNITVALUE'),
                    'labels': list(labels),
                    'table_path': list(tpath),
                })
    return facts


def _tables_with_path(root, tree, value, path):
    """표를 훑으면서 그 표까지의 제목 경로를 같이 준다.

    제목은 SECTION-*/COVER/CORRECTION 밑의 TITLE 뿐 아니라 TABLE-GROUP
    밑에도 온다 (holding/periodic 에서 확인된 사실 — 그걸 놓쳐서 제목
    136개 중 83개가 사라진 적이 있다). 그래서 직속 자식이 아니라
    descendant 로 훑는다 (절대 규칙 5).
    """
    stack = []

    def rec(node, depth, titles):
        for c in node.children:
            if c.tag in ('TITLE', 'COVER-TITLE'):
                t = value.flat(tree.text(c))
                if t:
                    titles = titles[:depth] + [t]
                continue
            if c.tag == 'TABLE':
                stack.append((c, list(titles)))
                continue          # 표 안으로 다시 안 들어간다
            rec(c, depth + 1, titles)

    rec(root, 0, [])
    return stack


def group_by_row(facts):
    """`TR` 단위 그룹핑. 명세가 요구한 모양.

    같은 (table_idx, row_idx) 의 값들을 한 묶음으로 준다. 키는
    4튜플이라 같은 행의 같은 ACODE 도 열로 구분된다.
    """
    rows = {}
    for f in facts:
        rows.setdefault((f['table_idx'], f['row_idx']), []).append(f)
    out = []
    for (ti, ri), fs in sorted(rows.items()):
        fs.sort(key=lambda f: f['col_idx'])
        out.append({
            'table_idx': ti,
            'row_idx': ri,
            'labels': fs[0]['labels'],
            'table_path': fs[0]['table_path'],
            'values': [{'col_idx': f['col_idx'], 'acode': f['acode'],
                        'value': f['value'], 'norm': f['norm'],
                        'tag': f['tag']} for f in fs],
        })
    return out


def key_of(fact):
    """유일 키. 명세의 3튜플이 아니라 4튜플이다 (모듈 머리말 참조)."""
    return (fact['table_idx'], fact['row_idx'], fact['col_idx'],
            fact['acode'])

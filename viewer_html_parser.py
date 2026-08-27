# -*- coding: utf-8 -*-
"""DART 공시뷰어 HTML(_viewer.html) → periodic_parser 가 먹는 트리로 바꾼다.

DART OpenAPI 가 document.xml 을 안 주는(status 014) 문서는 공시뷰어
HTML 로 대체 수집돼 있다. 그 HTML 은 XML 원문과 내용은 같지만 뼈대가
다르다 — 그 차이만 메워주면 periodic_parser 를 손대지 않고 그대로 쓸 수
있고, 결과 md 도 XML 로 만든 다른 분기 파일과 같은 모양이 된다.

    XML 원문                          공시뷰어 HTML
    ────────────────────────────────  ────────────────────────────────
    <SECTION-1><TITLE>제목</TITLE>    <P class="section-1">제목</P>
      ...내용...                        ...내용...  (형제로 나열)
    </SECTION-1>
    <SECTION-3><TITLE>2-1. …</TITLE>  <P class="table-group-xbrl">2-1. …</P>
    <COVER><COVER-TITLE>              <P class="cover-title">
    <TE>/<TU> (값 칸)                 <TD align="RIGHT|CENTER">
    THEAD                             THEAD (그대로 있음)
    rowspan/colspan                   rowspan/colspan (그대로 있음)

메울 구멍은 두 개다.

**① 섹션 중첩.** 뷰어 HTML 은 섹션을 컨테이너로 감싸지 않고 제목 문단을
형제로 흘려놓는다. 제목이 나온 지점을 경계로 뒤따르는 형제들을 그 섹션
안으로 집어넣어 준다. `table-group`/`table-group-xbrl` 도 제목 문단이다
(재무제표·XBRL 주석의 소제목) — XML 의 SECTION-3 자리에 대응한다.

**② 값 칸 표시(TE/TU).** periodic_parser 는 `_Cell.is_label` 을
`tag in ('TD','TH')` 로 판정한다. 뷰어 HTML 은 값 칸도 전부 <TD> 라 그냥
넣으면 **모든 칸이 라벨**이 되고, 그 결과 (ㄱ) 표 한 줄짜리 캡션이 문단이
아니라 제목으로 승격되고 (ㄴ) 라벨-값 쌍이 안 잡혀 kv 가 표로 밀린다.

값 칸은 `align` 으로 되살린다. 뷰어가 값을 오른쪽/가운데로 정렬해 그리기
때문이다. 실측(이 문서 TD 40,917칸)에서 신호가 깨끗하게 갈린다:

    align 없음   14,140칸 — 숫자     0칸 (0.0%)   → 라벨 (TD 유지)
    align=RIGHT  19,304칸 — 숫자 9,808칸          → 값   (TE 로 바꿈)
    align=CENTER  7,473칸 — 캡션·구분값           → 값   (TE 로 바꿈)

라벨 칸에 오른쪽 정렬이 걸린 경우도, 숫자 값에 정렬이 빠진 경우도 없다.
글자 모양(숫자처럼 생겼나)으로 추측하는 게 아니라 뷰어가 남긴 표시를
읽는 것이라, PDF 경로가 하던 휴리스틱과는 성격이 다르다.
"""

import re

import periodic_parser as pp

__all__ = ['parse_viewer_html', 'looks_like_viewer_html']

_RE_SECTION_CLASS = re.compile(r'^section-(\d+)$')

# periodic_parser._SECTION_TAGS 가 아는 최대 깊이
_MAX_SECTION = 4

# 재무제표·XBRL 주석 소제목. XML 의 SECTION-3 자리에 온다.
_TABLE_GROUP_CLASSES = {'table-group', 'table-group-xbrl'}
_TABLE_GROUP_LEVEL = 3

# 값 칸을 뜻하는 정렬. 이 정렬이 붙은 TD 는 XML 의 TE 에 해당한다.
_VALUE_ALIGNS = {'RIGHT', 'CENTER'}


def _new(tag, parent=None):
    return pp._Node(tag, {}, parent)


def _attach(parent, node):
    """부모에 자식으로 붙인다. children 과 raw 를 함께 유지해야 한다 —
    parser._walk 는 children 을, _text 는 raw 를 읽기 때문이다."""
    node.parent = parent
    parent.children.append(node)
    parent.raw.append(node)


def _titled(tag, title_tag, text, parent):
    """<SECTION-N><TITLE>text</TITLE> 짝을 만든다."""
    sec = _new(tag, parent)
    title = _new(title_tag, sec)
    title.raw.append(text)
    _attach(sec, title)
    return sec


def _class_of(node):
    return (node.attrs.get('CLASS') or '').strip()


def _restructure(body):
    """평면 body → SECTION-N 이 중첩된 body.

    스택으로 연다/닫는다. 같거나 더 얕은 레벨의 제목을 만나면 그 자리에서
    열려 있던 섹션들을 닫는다 (section-2 뒤에 section-1 이 오면 둘 다 닫힘).
    """
    out = _new('BODY')
    stack = [(0, out)]          # (레벨, 노드)

    for child in body.children:
        cls = _class_of(child)

        if cls == 'cover-title':
            text = pp.flat(pp._text(child))
            while len(stack) > 1:
                stack.pop()
            cover = _titled('COVER', 'COVER-TITLE', text, stack[-1][1])
            _attach(stack[-1][1], cover)
            stack.append((1, cover))     # section-1 이 오면 닫히도록 레벨 1
            continue

        m = _RE_SECTION_CLASS.match(cls)
        if m or cls in _TABLE_GROUP_CLASSES:
            lvl = (_TABLE_GROUP_LEVEL if m is None
                   else min(int(m.group(1)), _MAX_SECTION))
            text = pp.flat(pp._text(child))
            while len(stack) > 1 and stack[-1][0] >= lvl:
                stack.pop()
            sec = _titled('SECTION-%d' % lvl, 'TITLE', text, stack[-1][1])
            _attach(stack[-1][1], sec)
            stack.append((lvl, sec))
            continue

        _attach(stack[-1][1], child)

    return out


def _is_layout_table(tbl):
    """class='nb' = 테두리 없는 표. 뷰어가 데이터 격자가 아니라 표지·캡션·
    각주를 앉히는 레이아웃 상자로 쓴다 (이 문서 2,045개 중 1,434개)."""
    return 'nb' in (tbl.attrs.get('CLASS') or '').split()


def _rows_of(tbl):
    rows = []
    for tr in (e for e in pp._own_nodes(tbl) if e.tag == 'TR'):
        cells = [c for c in tr.children if c.tag in pp.CELL_TAGS]
        if cells:
            rows.append(cells)
    return rows


def _retag_value_cells(root):
    """레이아웃 표 안의 값 칸 <TD> 를 <TE> 로 바꾼다. 바꾼 칸 수를 돌려준다.

    테두리 있는 표(실데이터 격자)는 건드리지 않는다. 그쪽은 XML 에서도
    TD 로 채워져 있고, 머리글은 THEAD 가 이미 알려준다 — 손대면 멀쩡한
    표가 키-값 목록으로 흩어진다.

    값 칸으로 보는 건 **행을 통째로 차지하는 단일 칸** 하나뿐이다. 캡션·
    각주·단위 표기가 여기 온다 (`연결 재무상태표`, `(단위 : 천원)`, `※ …`).
    라벨로 두면 periodic_parser 가 제목으로 승격시켜 목차를 오염시킨다 —
    실측 419칸이 전부 `####` 제목이 돼 heading 이 159→379 로 부풀었다.

    정렬(align)만 보고 값 칸을 잡지는 않는다. 그렇게 하면 XML 이 라벨로
    두는 칸까지 값이 돼서, `당분기말 | (단위 : 천원)` 처럼 한 줄로 붙어야
    할 캡션 행이 `- **당분기말**: (단위 : 천원)` 키-값으로 흩어진다
    (실측 kv 50→483). 정렬은 값 칸과 상관이 높지만 XML 의 TD/TE 경계와
    같지는 않다.
    """
    n = 0
    for tbl in list(pp._walk(root)):
        if tbl.tag != 'TABLE' or not _is_layout_table(tbl):
            continue
        rows = _rows_of(tbl)
        if not rows:
            continue
        ncol = max(sum(pp._int(c.attrs.get('COLSPAN'), 1) for c in r)
                   for r in rows)
        for r in rows:
            if len(r) != 1 or r[0].tag != 'TD':
                continue
            if pp._int(r[0].attrs.get('COLSPAN'), 1) >= ncol:
                r[0].tag = 'TE'
                n += 1
    return n


def _convert_images(root):
    """<TABLE class='nb'> 안에 <IMG> 가 든 것을 <IMAGE> 노드로 바꾼다.

    XML 은 첨부 이미지를 <IMAGE><IMG><IMG-CAPTION> 으로 싣고,
    periodic_parser 는 그걸 '(첨부 이미지: 확인서)' 문단으로 낸다.
    뷰어 HTML 은 같은 것을 표에 앉혀 그린다.
    """
    n = 0
    for tbl in list(pp._walk(root)):
        if tbl.tag != 'TABLE':
            continue
        img = pp._find(tbl, 'IMG')
        if img is None:
            continue

        caption = None
        for node in pp._walk(tbl):
            if node.tag == 'P' and (node.attrs.get('CLASS') or '') == 'img-caption':
                caption = pp.flat(pp._text(node))
                break
        if not caption:
            # alt="이미지: 확인서" 에서 접두어를 떼고 쓴다.
            caption = re.sub(r'^\s*이미지\s*[:：]\s*', '',
                             img.attrs.get('ALT') or '').strip()

        image = _new('IMAGE', tbl.parent)
        cap = _new('IMG-CAPTION', image)
        cap.raw.append(caption or '?')
        _attach(image, cap)
        _replace(tbl, image)
        n += 1
    return n


def _replace(old, new):
    """부모의 children/raw 에서 old 를 new 로 갈아끼운다."""
    p = old.parent
    if p is None:
        return
    new.parent = p
    for seq in (p.children, p.raw):
        for i, x in enumerate(seq):
            if x is old:
                seq[i] = new
                break


def looks_like_viewer_html(source):
    """껍데기(JS가 iframe에 본문을 늦게 싣는 뷰어 페이지)와 구별한다.

    껍데기는 <script>/<iframe> 만 잔뜩이고 본문 표가 없다. 전문 파일은
    section-N 제목과 표를 실제로 갖고 있다.
    """
    if '<iframe' in source.lower():
        return False
    return re.search(r'class=["\']section-\d+["\']', source) is not None


def parse_viewer_html(source, receipt_no=None, corp_name=None,
                      drop_empty=True, show_iso_date=True, stats=None):
    """뷰어 HTML 한 건 → periodic_parser 와 같은 doc dict.

    periodic_parser.to_markdown(doc) 에 그대로 넣을 수 있다.
    """
    b = pp._TreeBuilder()
    b.feed(source)
    root = b.root

    body = pp._find(root, 'BODY') or root
    _convert_images(body)
    _retag_value_cells(body)
    body = _restructure(body)

    parser = pp.PeriodicParser(drop_empty=drop_empty,
                               show_iso_date=show_iso_date, stats=stats)
    chunks = []
    parser._walk(body, chunks, depth=0, ctx=[], skip=None)

    title = pp._find(root, 'TITLE')
    doc_name = pp.flat(pp._text(title)) if title is not None else None

    return {
        '회사명': corp_name,
        '제출인_법인명': corp_name,
        '문서종류': doc_name,
        '서식버전': None,
        '접수번호': receipt_no,
        '정정공시': False,
        'chunks': chunks,
    }

# -*- coding: utf-8 -*-
"""사업/분기/반기보고서 및 첨부서류(periodic) DSD-XML → 마크다운 변환기.

대상: corpus/raw/periodic/**/*.xml  (1,466건. *_viewer.html 3건은 DART 뷰어
빈 껍데기라 원본 XML 자체가 없다 — looks_like_periodic_xml()이 걸러낸다)

major_parser.py 와 같은 골격(관대한 HTML 파서, 셀 모델, 행 분류 알고리즘)을
그대로 쓰되, periodic 원문을 전수 훑어 확인한 사실에 맞게 두 가지를 더했다.
(아래 "major_parser.py 와 다른 점" 참조. holding_parser.py 와 같은 개선이다 —
periodic 쪽이 규모가 훨씬 커서 효과도 훨씬 크다.)

표준 라이브러리만 쓴다. 설치할 게 없다.

────────────────────────────────────────────────────────────────────────
원본 1,466건을 전부 훑어서 확인한 사실
────────────────────────────────────────────────────────────────────────
 · 인코딩  : 1,466/1,466 UTF-8. cp949 폴백은 방어용.
 · 태그    : major와 같은 DSD-XML 스키마(TD/TH/TE/TU, SECTION-1~4, TABLE-GROUP …).
 · 정정    : <CORRECTION> 태그 유무 = manifest의 is_correction. 160건.
 · 중첩표  : 628건(43%) — holding(5건)과 비교가 안 될 만큼 흔하다.
    감사보고서 재무제표 주석에 표 안에 표(예: 리스부채 만기분석)가 잦다.
 · 한 접수번호에 원문이 여러 개다(최대 3개) — 본보고서 + 감사보고서
   (00760) + 연결감사보고서(00761) 등. **파서는 파일 하나만 본다**;
   여러 파일 묶기는 rag_pipeline.py(build_output_name)가 접수번호 뒤에
   꼬리(_00760)를 붙여 처리한다.
 · 문서종류 상위: 분기보고서 527 / 사업보고서 290 / 반기보고서 234 /
   감사보고서 208 / 연결감사보고서 207.

┌ 표 4분류 (1,538,775개) ────────────────────────────────────────────────┐
│ ① THEAD 있음    521,545 (34%)  → 표 그대로 유지                        │
│ ② TE/TU 있음     407,919 (27%)  → - **항목**: 값                       │
│ ③ 1행 1칸        444,036 (29%)  → 제목/문단                            │
│ ④ TD만           165,275 (11%)  → 재무제표 주석 표 다수 — 예외① 참조    │
└───────────────────────────────────────────────────────────────────────┘

 · rowspan 최대 838(!), rowspan+colspan 동시 21,516군데.

────────────────────────────────────────────────────────────────────────
major_parser.py 와 다른 점 (둘 다 원문 전수 확인 후 반영)
────────────────────────────────────────────────────────────────────────
 ① 머리글 없는 TD표(④, 165,275개)에 재무제표 주석 표가 아주 많다.
    THEAD가 아니라 배경 음영(USERMARK 안 "BC0X..." 코드)으로만 머리글을
    표시한다 — 예: "구분/당기말/전기말" 아래 "구분/유동/비유동/유동/비유동"
    두 줄짜리 머리글. 이 신호는 TD에만 붙고 TE/TU에는 전혀 안 붙는다
    (15개사 표본에서 TD 106만 건 중 음영 5.6만 건, TE/TU 0건 확인).
    놓치면 "구분1/구분2/…" 같은 가짜 머리글로 "당기말 유동" 같은 실제
    항목명이 사라지고, 재무제표 숫자가 아무 맥락 없는 표가 된다 —
    periodic에서 가장 값어치 있는 데이터가 이 표들이라 영향이 크다.
    → _flush_buffer() 가 버퍼 맨 위 몇 줄이 전부 머리글 표시(TH 또는
      음영 TD)인지 먼저 보고, 있으면(여러 줄이면 합쳐서) 진짜 머리글로 쓴다.
 ② major_parser.py의 _live_columns()는 "모든 버퍼 행에서 옆칸과 완전히
    같은 열"만 없앤다. 그런데 표지 레터헤드처럼 colspan 위치가 행마다
    다르면(어떤 행은 1~2열 합침, 어떤 행은 안 합침) 이 조건이 성립하지
    않아 옆칸에 같은 글자가 그대로 중복 출력된다.
    → _render_row() 는 표 전체가 아니라 **행 하나 안에서** "바로 왼쪽
      칸과 같은 셀 객체인가"만 보고 지운다. 행마다 판단하므로 colspan
      위치가 들쭉날쭉해도 항상 맞게 지워진다.
"""

import re
from html.entities import html5 as _HTML5_ENTITIES
from html.parser import HTMLParser

# ── 2단계: 공통 층 (src/normalize/) ─────────────────────────────────
# 아래 이름들은 major/holding/periodic 세 파서에서 글자 한 자 다르지 않아
# src/normalize/ 로 옮겼다(scripts/02_diff_parsers.py 로 확인). 이름과
# 동작은 그대로다 — 여기서 import 만 한다.
import _srcpath  # noqa: F401  (src/ 를 sys.path 에 얹는다)

from normalize.value import (                       # noqa: F401
    RE_MULTISPACE as _RE_MULTISPACE,
    RE_INVISIBLE as _RE_INVISIBLE,
    EMPTY_VALUES as _EMPTY_VALUES,
    RE_ISO8 as _RE_ISO8,
    RE_ISO_RANGE as _RE_ISO_RANGE,
    RE_COLON_LABEL as _RE_COLON_LABEL,
    clean, flat, is_empty_value,
    to_int as _int,
    escape_cell as _esc,
)
from normalize.tree import (                        # noqa: F401
    Node as _Node,
    CELL_TAGS,
    SECTION_TAGS as _SECTION_TAGS,
    IGNORE as _IGNORE,
    walk as _walk,
    text as _text,
    own_nodes as _own_nodes,
    own_tables as _own_tables,
    find as _find,
    in_thead as _tree_in_thead,
)
from normalize.encoding import decode_text as decode  # noqa: F401
from normalize.grid import expand as _grid_expand      # noqa: F401

# ── 2단계: render 층 (src/render/) ──────────────────────────────────
from render.markdown import (                       # noqa: F401
    render_dsd as _render_dsd,
    iso_from_aunitvalue as _iso_impl,
)
# ────────────────────────────────────────────────────────────────────
# ────────────────────────────────────────────────────────────────────

__all__ = ['parse_file', 'parse', 'to_markdown', 'to_dict', 'decode',
           'PeriodicParser', 'Stats', 'corp_name_from_path',
           'looks_like_periodic_xml']


# ══════════════════════════════════════════════════════════════════════
# 1. 인코딩
# ══════════════════════════════════════════════════════════════════════



# ══════════════════════════════════════════════════════════════════════
# 2. 글자 청소
# ══════════════════════════════════════════════════════════════════════









# ══════════════════════════════════════════════════════════════════════
# 3. 관대한 트리 파서
# ══════════════════════════════════════════════════════════════════════



class _TreeBuilder(HTMLParser):
    """html.parser 위에 부모-자식 트리를 얹는다.

    XML 파서(xml.etree 등)를 쓰지 않는 이유: 본문에 이스케이프 안 된 '<'가
    섞여 들어간 문서가 있다. html.parser는 '<' 다음이 영문자일 때만
    태그로 보므로(한글/숫자/기호가 오면 그냥 글자) 이런 문서도 안 깨진다.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = _Node('#root', {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = _Node(tag.upper(), {k.upper(): v for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        self.cur.raw.append(n)
        self.cur = n

    def handle_startendtag(self, tag, attrs):
        n = _Node(tag.upper(), {k.upper(): v for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        self.cur.raw.append(n)

    def handle_endtag(self, tag):
        t = tag.upper()
        n = self.cur
        while n is not self.root and n.tag != t:
            n = n.parent            # 안 닫힌 태그 방어
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, d):
        self.cur.raw.append(d)

    def handle_entityref(self, name):
        if (name + ';') in _HTML5_ENTITIES or name in _HTML5_ENTITIES:
            self.cur.raw.append(_HTML5_ENTITIES.get(name + ';')
                                or _HTML5_ENTITIES.get(name) or ('&%s;' % name))
        else:
            self.cur.raw.append('&%s' % name)

    def handle_charref(self, name):
        try:
            self.cur.raw.append(chr(int(name[1:], 16) if name[:1].lower() == 'x'
                                    else int(name)))
        except ValueError:
            self.cur.raw.append('&#%s;' % name)

    def handle_comment(self, d):
        pass






def _para_text(node):
    """문단(<P>) 안의 글자를 모으되, **태그 경계**에서 공백이 전혀 없으면
    한 칸 끼운다. 원문이 굵게 표시한 소제목 <SPAN>을 바로 뒤 문장에 공백
    없이 붙여 쓰는 경우가 있다 — 예: <SPAN>나. 소수주주권</SPAN>회사는...
    그대로 이어 붙이면 "나. 소수주주권회사는"처럼 라벨이 문장에 녹아들어
    회사 이름처럼 오독된다.

    ⚠️ node.raw의 조각 경계가 전부 '진짜' 태그 경계는 아니다. html.parser는
    이스케이프 안 된 '&'를 만나면(예: "P&A인수하여") 그 앞뒤 글자를 별개
    조각으로 쪼갠다 — 실제 원문엔 공백이 없는데도. 그래서 두 조각
    **모두** 문자열이면(둘 다 태그에서 나온 게 아니면) 공백을 넣지 않는다.
    조각 중 하나라도 <SPAN> 등 태그에서 나왔을 때만 공백 경계로 본다.

    표 칸(_text_no_table)에는 적용하지 않는다 — "1,234"+"백만원"처럼
    붙어야 자연스러운 숫자·단위 조합이 흔해서다.
    """
    parts = [(_text(r), True) if isinstance(r, _Node) else (r, False)
             for r in node.raw]
    out = []
    prev_from_tag = False
    for text, from_tag in parts:
        if (out and text and out[-1] and (prev_from_tag or from_tag)
                and not out[-1][-1].isspace() and not text[0].isspace()):
            out.append(' ')
        out.append(text)
        prev_from_tag = from_tag
    return ''.join(out)


def _text_no_table(node):
    """셀 안에 표가 들어있을 때, 그 표의 글자는 빼고 읽는다."""
    out = []
    for r in node.raw:
        if isinstance(r, _Node):
            if r.tag == 'TABLE':
                continue
            out.append(_text_no_table(r))
        else:
            out.append(r)
    return ''.join(out)








# ══════════════════════════════════════════════════════════════════════
# 4. 표 한 칸
# ══════════════════════════════════════════════════════════════════════


# 머리글이 THEAD 없이 배경 음영만으로 표시된 경우의 신호.
# TD의 USERMARK 안에 이 문자열이 있으면 머리글 칸이다 (TE/TU엔 붙지 않음).
_RE_SHADED = re.compile(r'BC0X')


class _Cell:
    __slots__ = ('text', 'tag', 'rowspan', 'colspan', 'origin_row',
                 'code', 'norm', 'is_header')

    def __init__(self, node, row):
        self.tag = node.tag
        self.text = flat(_text_no_table(node))   # 중첩 표 글자는 뺀다
        self.rowspan = _int(node.attrs.get('ROWSPAN'), 1)
        self.colspan = _int(node.attrs.get('COLSPAN'), 1)
        self.origin_row = row
        self.code = node.attrs.get('ACODE') or node.attrs.get('AUNIT')
        self.norm = node.attrs.get('AUNITVALUE')
        self.is_header = (node.tag == 'TH' or
                          (node.tag == 'TD'
                           and bool(_RE_SHADED.search(node.attrs.get('USERMARK', '')))))

    @property
    def is_label(self):
        return self.tag in ('TD', 'TH')

    @property
    def is_empty(self):
        return not self.text.strip()




# ══════════════════════════════════════════════════════════════════════
# 5. 통계 (검증용)
# ══════════════════════════════════════════════════════════════════════

class Stats:
    def __init__(self):
        self.tables = {}
        self.rows = {}
        self.notes = {}

    def bump(self, box, key):
        box[key] = box.get(key, 0) + 1

    def as_dict(self):
        return {'표': self.tables, '행': self.rows, '참고': self.notes}


# ══════════════════════════════════════════════════════════════════════
# 6. 파서
# ══════════════════════════════════════════════════════════════════════

_RE_BRACKET_TITLE = re.compile(r'^\s*[\u3010\[]')
_RE_NUM_TITLE = re.compile(r'^\s*\d+\s*[.\uff0e)]\s*\S')



class PeriodicParser:
    """periodic XML 하나를 조각(chunk) 목록으로 바꾼다.

    조각 종류
        ('h',  단계, 글자)                 제목
        ('p',  글자)                       문단
        ('kv', [키조각들], 값, 코드, 정규화값)  키-값
        ('t',  [머리글], [[행]])            표
    """

    def __init__(self, drop_empty=True, show_iso_date=True, stats=None):
        self.drop_empty = drop_empty
        self.show_iso_date = show_iso_date
        self.stats = stats

    def _cell_text(self, cell):
        """표 칸의 글자. TU의 정리된 날짜가 화면 글자와 다르면 덧붙인다."""
        text = cell.text
        if not self.show_iso_date:
            return text
        iso = _iso(cell.norm)
        if iso and iso not in text:
            return '%s (%s)' % (text, iso) if text else iso
        return text

    # ── 진입점 ────────────────────────────────────────────────────────
    def parse(self, source, receipt_no=None, corp_name=None):
        b = _TreeBuilder()
        b.feed(source)
        root = b.root

        doc_name = self._tag_text(root, 'DOCUMENT-NAME')
        company = self._tag_text(root, 'COMPANY-NAME')
        version = self._tag_text(root, 'FORMULA-VERSION')

        body = _find(root, 'BODY') or root
        chunks = []
        self._walk(body, chunks, depth=0, ctx=[], skip=None)

        is_corr = _find(root, 'CORRECTION') is not None

        return {
            '회사명': corp_name or company,
            '제출인_법인명': company,
            '문서종류': doc_name,
            '서식버전': version,
            '접수번호': receipt_no,
            '정정공시': is_corr,
            'chunks': chunks,
        }

    def _tag_text(self, root, tag):
        n = _find(root, tag)
        return flat(_text(n)) if n is not None else None

    # ── 문서 훑기 ─────────────────────────────────────────────────────
    def _walk(self, node, out, depth, ctx, skip=None):
        """skip: 부모(SECTION/COVER/CORRECTION)가 제목으로 이미 뽑아 쓴
        TITLE/COVER-TITLE 노드. 이 노드는 다시 훑을 때 건너뛴다.

        ⚠️ TITLE이 SECTION-N/COVER/CORRECTION의 '바로 밑'에만 있는 게
        아니다. XBRL 연동 재무제표 주석은 TABLE-GROUP 밑에 TITLE이 바로
        오는 경우가 실제 확인 결과 61%(HMM 분기보고서 표본, 136개 중
        83개)에 달한다. 예전엔 "TITLE은 무조건 위에서 이미 소비했다"고
        가정하고 전부 건너뛰어서, 재무제표 주석 제목(예: "10. 기타금융
        자산(연결)")과 목차 표제(BODY 바로 밑)가 통째로 사라졌다.
        그래서 '정확히 이 부모가 방금 뽑아 쓴 그 노드'만 skip으로
        지정해 건너뛰고, 그 밖의 TITLE은 전부 실제 제목으로 낸다.
        """
        for c in node.children:
            if c is skip:
                continue
            t = c.tag

            if t in _IGNORE:
                continue

            if t == 'CORRECTION':
                title_node = self._child_title_node(c)
                title = flat(_text(title_node)) if title_node is not None else None
                title = title or '정정신고(보고)'
                out.append(('h', 2, title))
                self._walk(c, out, 2, [title], skip=title_node)
                continue

            if t in _SECTION_TAGS:
                lvl = _SECTION_TAGS[t]
                title_node = self._child_title_node(c)
                title = flat(_text(title_node)) if title_node is not None else None
                sub = list(ctx)
                if title:
                    out.append(('h', lvl, title))
                    sub = [title]
                self._walk(c, out, lvl, sub, skip=title_node)
                continue

            if t == 'COVER':
                title_node = self._child_title_node(c)
                title = flat(_text(title_node)) if title_node is not None else None
                if title:
                    out.append(('h', 2, title))
                self._walk(c, out, 2, [title] if title else list(ctx), skip=title_node)
                continue

            if t in ('TITLE', 'COVER-TITLE'):
                # 부모가 이미 소비한 게 아니면 진짜 제목이다 (예외 참조).
                # BODY 바로 밑(depth=0)의 목차 같은 표제가 SECTION-1(레벨2)
                # 보다 얕은 레벨1이 되지 않도록 최소 레벨을 2로 맞춘다.
                text = flat(_text(c))
                if text:
                    out.append(('h', min(max(depth + 1, 2), 6), text))
                continue

            if t == 'TABLE':
                out.extend(self._table(c, depth))
                continue                      # 표 안으로 다시 안 들어간다

            if t == 'P':
                s = clean(_para_text(c))
                if s:
                    out.append(('p', s))
                continue

            if t == 'IMAGE':
                img = _find(c, 'IMG')
                cap = _find(c, 'IMG-CAPTION')
                out.append(('p', '(첨부 이미지: %s)'
                            % (flat(_text(cap)) if cap is not None
                               else flat(_text(img)) if img is not None else '?')))
                continue

            self._walk(c, out, depth, ctx)

    def _child_title_node(self, node):
        for c in node.children:
            if c.tag in ('TITLE', 'COVER-TITLE'):
                return c
        return None

    # ── 표 처리 ───────────────────────────────────────────────────────
    def _table(self, table, depth):
        trs = [e for e in _own_nodes(table) if e.tag == 'TR']
        raw = []
        head_rows = 0
        for tr in trs:
            cells = [c for c in tr.children if c.tag in CELL_TAGS]
            if cells:
                raw.append(cells)
                if self._in_thead(tr):
                    head_rows += 1

        nested = []
        for inner in _own_tables(table):
            nested.extend(self._table(inner, depth + 1))
        if self.stats and nested:
            self.stats.bump(self.stats.notes, '중첩표처리')

        if not raw:
            return nested

        has_thead = head_rows > 0
        has_value = any(c.tag in ('TE', 'TU') for row in raw for c in row)
        n_cells = sum(len(r) for r in raw)

        if has_thead:
            kind = '①진짜표'
        elif has_value:
            kind = '②키값표'
        elif len(raw) == 1 and n_cells == 1:
            kind = '③1칸표'
        else:
            kind = '④TD만'
        if self.stats:
            self.stats.bump(self.stats.tables, kind)

        if kind == '①진짜표':
            return self._real_table(raw, head_rows) + nested
        if kind == '③1칸표':
            return self._one_cell(flat(_text_no_table(raw[0][0])), depth) + nested
        return self._kv_table(raw) + nested

    def _in_thead(self, tr):
        """(본체는 normalize/tree.py — 세 파서에서 완전 동일했다.)"""
        return _tree_in_thead(tr)

    # ── ③ 1칸 표 ─────────────────────────────────────────────────────
    def _one_cell(self, text, depth):
        if not text:
            return []
        if _RE_BRACKET_TITLE.match(text) or _RE_NUM_TITLE.match(text):
            if self.stats:
                self.stats.bump(self.stats.rows, '1칸표_제목')
            return [('h', min(depth + 1, 6), text)]
        if self.stats:
            self.stats.bump(self.stats.rows, '1칸표_문단')
        return [('p', text)]

    # ── ① 진짜 표 (THEAD 있음) ───────────────────────────────────────
    def _real_table(self, raw, head_rows):
        """THEAD 가 있으면 표를 그대로 유지한다.

        ⚠️ 이 안에서는 TD/TE/TU 를 구분하지 않는다.
        THEAD 표의 데이터 행은 전부 TD 로 되어 있는 경우가 흔하다.
        """
        grid, ncol = self._expand(raw)
        keep = self._live_columns(grid, ncol)

        headers = []
        for c in keep:
            parts = []
            for r in range(head_rows):
                cell = grid[r][c] if r < len(grid) else None
                if cell and cell.text and cell.text not in parts:
                    parts.append(cell.text)
            headers.append(' '.join(parts) or '구분')

        rows = []
        for r in range(head_rows, len(grid)):
            line = [self._cell_text(grid[r][c]) if grid[r][c] else '' for c in keep]
            if any(x.strip() for x in line):
                rows.append(line)

        if self.stats:
            self.stats.bump(self.stats.rows, '진짜표')
        if not rows:
            return []
        return [('t', headers, rows)]

    def _live_columns(self, rows, ncol):
        """colspan 때문에 옆칸과 완전히 같은 열은 버린다."""
        keep = []
        for c in range(ncol):
            if c > 0 and rows and all(
                    (c < len(row) and row[c] is not None
                     and row[c] is row[c - 1]) for row in rows):
                continue
            keep.append(c)
        return keep

    # ── ②④ 키-값 표 / TD만 표 ───────────────────────────────────────
    def _kv_table(self, raw):
        out = []
        grid, ncol = self._expand(raw)
        buf = []                      # 표로 내보낼 '행 번호' 버퍼

        def flush():
            if buf:
                out.extend(self._flush_buffer(grid, ncol, buf))
                buf.clear()

        for r in range(len(grid)):
            own, inherited = self._logical(grid, ncol, r)

            if not own or all(c.is_empty for c in own):
                if self.stats:
                    self.stats.bump(self.stats.rows, '빈행')
                continue

            full = (len(own) == 1 and not inherited
                    and own[0].colspan >= ncol)

            if full and own[0].is_label:
                flush()
                out.append(('h', 4, own[0].text))
                if self.stats:
                    self.stats.bump(self.stats.rows, '섹션제목')
                continue

            if full:
                flush()
                if not (self.drop_empty and is_empty_value(own[0].text)):
                    out.append(('p', own[0].text))
                if self.stats:
                    self.stats.bump(self.stats.rows, '값1칸')
                continue

            kvs = self._to_kv(own, inherited)
            if kvs:
                flush()
                out.extend(kvs)
            else:
                buf.append(r)

        flush()
        return out

    # ── 버퍼(키값이 아닌 라벨 행들)를 표 또는 문단으로 낸다 ────────────
    def _render_row(self, grid_row, ncol):
        """행 하나를 렌더링한다. colspan으로 옆칸까지 차지한 글자는
        첫 칸에만 남기고 나머지는 비운다 — **이 행 안에서만** 판단하므로
        표 전체에서 colspan 위치가 들쭉날쭉해도 항상 맞게 지워진다.
        """
        out, prev = [], None
        for c in range(ncol):
            cell = grid_row[c] if c < len(grid_row) else None
            out.append(self._cell_text(cell) if (cell and cell is not prev) else '')
            prev = cell
        return out

    def _flush_buffer(self, grid, ncol, buf):
        """버퍼에 쌓인 '표 모양은 아닌 라벨 행들'을 표/문단으로 낸다.

        머리글이 THEAD 없이 음영으로만 표시된 표(예외①)를 여기서 잡는다.
        버퍼 맨 위부터, 빈 칸이 아닌 칸이 전부 머리글 표시(TH 또는 음영
        TD)인 행이 이어지는 동안만 머리글로 본다. 그런 행이 없으면
        major_parser.py와 같이 '구분1/구분2/…'로 대체한다.
        """
        rows_raw = [grid[r] for r in buf]

        n_header = 0
        for row in rows_raw:
            filled = [c for c in row[:ncol] if c and not c.is_empty]
            if not filled or not all(c.is_header for c in filled):
                break
            n_header += 1
        # 표 하나가 통째로 머리글처럼 보이면(데이터 행이 없으면) 머리글로
        # 안 본다 — '구분1…' 대체가 아니라 그냥 문단/표로 흘려보낸다.
        if n_header >= len(rows_raw):
            n_header = 0

        if n_header:
            headers = []
            for c in range(ncol):
                parts = []
                for row in rows_raw[:n_header]:
                    cell = row[c] if c < len(row) else None
                    text = cell.text.strip() if cell else ''
                    if text and (not parts or parts[-1] != text):
                        parts.append(text)
                headers.append(' '.join(parts))
            data = [self._render_row(row, ncol) for row in rows_raw[n_header:]]
            if self.stats:
                self.stats.bump(self.stats.rows, '음영머리글표')
            return [('t', headers, data)]

        keep = self._live_columns(rows_raw, ncol)
        rows = [[self._cell_text(row[c]) if c < len(row) and row[c] else ''
                 for c in keep] for row in rows_raw]
        if len(rows) >= 2:
            return [('t', ['구분%d' % (i + 1) for i in range(len(keep))], rows)]
        return [('p', ' | '.join(x for x in rows[0] if x.strip()))]

    def _expand(self, raw):
        """rowspan / colspan 을 펼쳐 빈틈 없는 2차원 표로 만든다.
        (본체는 normalize/grid.py — 세 파서에서 완전 동일했다.)"""
        return _grid_expand(raw, _Cell)

    def _logical(self, grid, ncol, r):
        """이 행이 새로 만든 칸과, 위에서 물려받은 대분류를 나눈다."""
        own, inherited, prev = [], [], None
        for c in range(ncol):
            cell = grid[r][c] if c < len(grid[r]) else None
            if cell is None or cell is prev:
                continue
            prev = cell
            if cell.origin_row != r:
                if cell.is_label and not cell.is_empty:
                    inherited.append(cell)
                continue
            if (own and own[-1].is_label and cell.is_label
                    and own[-1].text == cell.text):
                continue
            own.append(cell)
        return own, inherited

    def _to_kv(self, own, inherited):
        """칸들을 [라벨…][값] 런으로 잘라 키-값을 만든다.

        L V / L L V / L V L V 를 같은 규칙으로 처리하고,
        값이 먼저 오는 V L 도 뒤집어서 받는다.
        """
        ctx = [c.text for c in inherited]

        runs, cur = [], None
        for c in own:
            k = 'L' if c.is_label else 'V'
            if cur and cur[0] == k:
                cur[1].append(c)
            else:
                cur = [k, [c]]
                runs.append(cur)

        if len(runs) == 1 and runs[0][0] == 'L':
            return self._colon_kv(own, ctx)

        out, i = [], 0
        matched = False
        # 첫 L런이 라벨 2개 이상("L L V…")이면 마지막 하나만 소분류로 보고
        # 앞쪽은 이 행 전체의 대분류로 승격해 뒤따르는 쌍에도 물려준다.
        # ⚠️ 라벨 두 개가 대등한 열(예: "구분"/"항목")인 표에도 이 규칙을
        # 적용하면 없는 계층이 생길 수 있다 — 지금까지는 항상 대분류/
        # 소분류 관계였다.
        row_ctx = []
        while i < len(runs):
            kind, cells = runs[i]
            nxt = runs[i + 1] if i + 1 < len(runs) else None

            if kind == 'L' and nxt and nxt[0] == 'V':
                labels = [c.text for c in cells if not c.is_empty]
                if not matched and len(labels) >= 2:
                    row_ctx = labels[:-1]
                    labels = labels[-1:]
                # "L V L"으로 행이 끝나면(예: 사업연도/값/부터) 마지막 라벨은
                # 새 항목이 아니라 방금 짝지은 값의 접미사다(부터·까지 등).
                # 뒤에 더 이어지는 값이 없을 때만 그렇게 본다 — "L V L V"처럼
                # 뒤에 진짜 다음 쌍이 있으면 건드리지 않는다.
                after = runs[i + 2] if i + 2 < len(runs) else None
                consumed = 2
                if after is not None and after[0] == 'L' and i + 3 >= len(runs):
                    labels = labels + [c.text for c in after[1] if not c.is_empty]
                    consumed = 3
                for v in nxt[1]:
                    out.extend(self._emit(ctx + row_ctx, labels, v))
                matched = True
                i += consumed
            elif kind == 'V' and nxt and nxt[0] == 'L':
                labels = [c.text for c in nxt[1] if not c.is_empty]
                for v in cells:
                    out.extend(self._emit(ctx + row_ctx, labels, v))
                if self.stats:
                    self.stats.bump(self.stats.notes, '역순키값')
                matched = True
                i += 2
            else:
                i += 1

        # runs는 항상 L/V가 번갈아 나온다(같은 종류는 만들 때 이미 합쳤으므로).
        # 그래서 위 while이 앞에서부터 짝을 지어 소비하고 나면, 개수가
        # 홀수일 때 **마지막 run 하나만** 짝 없이 남는다. 라벨(L)이면
        # 값이 없으니 버려도 되지만, 값(V)이면 라벨이 없다고 그냥
        # 버리면 안 된다 — rowspan으로 물려받은 대분류(ctx)가 있으면
        # 그것만으로라도 키를 만든다. (감사보고서 "감사업무 수행내용"
        # 표처럼 같은 열의 라벨이 훨씬 위쪽 행에서 rowspan으로만 걸려
        # 있고 이 행 자체엔 라벨 칸이 아예 없는 경우가 실제로 있다)
        if runs and runs[-1][0] == 'V' and len(runs) % 2 == 1:
            for v in runs[-1][1]:
                out.extend(self._emit(ctx + row_ctx, [], v))
            if self.stats:
                self.stats.bump(self.stats.notes, '라벨없는_꼬리값')

        if self.stats and matched:
            self.stats.bump(self.stats.rows, '키값')
        return out

    def _colon_kv(self, own, ctx):
        """표지의  L"보고자 :"  L"국민연금공단"  형태."""
        if len(own) != 2:
            return []
        m = _RE_COLON_LABEL.match(own[0].text)
        if not m:
            return []
        key = re.sub(r'\s+', '', m.group(1))
        if self.stats:
            self.stats.bump(self.stats.rows, '표지콜론키값')
        return self._emit(ctx, [key], own[1])

    def _emit(self, ctx, labels, cell):
        v = cell.text
        if self.drop_empty and is_empty_value(v):
            return []
        if not labels and not ctx:
            return []
        return [('kv', ctx + labels, None if is_empty_value(v) else v,
                 cell.code, cell.norm)]


# ══════════════════════════════════════════════════════════════════════
# 7. 마크다운
# ══════════════════════════════════════════════════════════════════════



def _iso(norm):
    """AUNITVALUE 를 사람이 읽을 날짜로. 4가지 형식뿐임을 확인했다.
    (본체는 render/markdown.py — 세 파서에서 동일했다.)"""
    return _iso_impl(norm, _RE_ISO8, _RE_ISO_RANGE)




def to_markdown(doc, with_header=True):
    """조각 목록 → 마크다운. 본체는 render/markdown.py:render_dsd.

    DSD 세 파서의 원본은 기본 제목 문자열 하나만 달랐다. 그것만 넘긴다.
    """
    return _render_dsd(doc, '사업보고서', _iso, _esc, with_header)


# ══════════════════════════════════════════════════════════════════════
# 8. 키-값 사전 (덤)
# ══════════════════════════════════════════════════════════════════════

def to_dict(doc):
    fields, codes, tables, texts, seen = {}, {}, [], [], {}
    for c in doc['chunks']:
        if c[0] == 'kv':
            _, parts, value, code, norm = c
            key = ' > '.join(parts)
            n = seen.get(key, 0) + 1
            seen[key] = n
            if n > 1:
                key = '%s#%d' % (key, n)
            fields[key] = value
            if code:
                codes[key] = {'코드': code, '원본값': norm} if norm else {'코드': code}
        elif c[0] == 't':
            tables.append({'머리글': c[1],
                           '행': [dict(zip(c[1], r)) for r in c[2]]})
        elif c[0] == 'p':
            texts.append(c[1])
    return {
        '회사명': doc['회사명'],
        # <COMPANY-NAME> 원문값 (periodic에서는 보고 대상 회사(자기 자신) 등기명).
        '제출인_법인명': doc.get('제출인_법인명'),
        '법인명_다름': bool(doc.get('제출인_법인명')
                        and doc.get('제출인_법인명') != doc['회사명']),
        '문서종류': doc['문서종류'],
        '서식버전': doc['서식버전'],
        '접수번호': doc['접수번호'],
        '정정공시': doc['정정공시'],
        '항목': fields,
        '필드코드': codes,
        '표': tables,
        '본문': texts,
    }


# ══════════════════════════════════════════════════════════════════════
# 9. 편의 함수
# ══════════════════════════════════════════════════════════════════════

def corp_name_from_path(path):
    """raw/periodic/<법인명>/<접수번호>_<분기유형>_<연>_<월>/<접수번호>[_첨부번호].xml
    에서 법인명을 뽑는다. major/holding과 같은 2단계 위 규칙이다."""
    import os
    return os.path.basename(os.path.dirname(os.path.dirname(path)))


def looks_like_periodic_xml(source):
    return '<DOCUMENT' in source[:600].upper()


def parse(source, receipt_no=None, corp_name=None, **kw):
    return PeriodicParser(**kw).parse(source, receipt_no, corp_name)


def parse_file(path, receipt_no=None, corp_name=None, **kw):
    import os
    source = decode(open(path, 'rb').read())
    if receipt_no is None:
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r'^\d{14}', base)
        receipt_no = m.group(0) if m else None
    if corp_name is None:
        corp_name = corp_name_from_path(path)
    return PeriodicParser(**kw).parse(source, receipt_no, corp_name)

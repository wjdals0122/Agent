# -*- coding: utf-8 -*-
"""주요사항보고서(major) DSD-XML → 마크다운 변환기.

대상: corpus/raw/major/**/*.xml  (598건)
거래소공시(exchange)와는 완전히 다른 형식이라 파서를 따로 둔다.

표준 라이브러리만 쓴다. 설치할 게 없다.

────────────────────────────────────────────────────────────────────────
원본 598건을 전부 훑어서 확인한 사실
────────────────────────────────────────────────────────────────────────
 · 인코딩  : 전부 UTF-8 (4,616개 전수 확인)
 · 라벨/값 : TD/TH = 라벨, TE/TU = 값.
             TE 56,740개 전부 ACODE 보유, TU 6,330개 전부 AUNITVALUE 보유.
             단 **THEAD 있는 표 안에서는 TD도 데이터다** (4,404행). 예외② 참조.
 · 태그    : 33종으로 완결. 미지정 태그 0개.
 · 행      : 47,452행이 아래 분류로 완결. 미분류 0개.
 · 표      : 5,609개가 4유형으로 완결.
 · 정정    : <CORRECTION> 태그 유무 = manifest의 is_correction (598/598 일치)

┌ 표 4분류 ─────────────────────────────────────────────────────────────┐
│ ① THEAD 있음      1,267 (23%)  → 표 그대로 유지                        │
│ ② TE/TU 있음      1,326 (24%)  → - **항목**: 값                       │
│ ③ 1행 1칸         1,538 (27%)  → 제목 962 / 번호제목 359 / 각주 124 …  │
│ ④ TD만            1,478 (26%)  → 표지·목록                            │
└───────────────────────────────────────────────────────────────────────┘

┌ 행 분류 (미분류 0) ───────────────────────────────────────────────────┐
│ 키값(단일)    16,439   L…V           → - **키**: 값                    │
│ 진짜표 행     14,420                 → 표 유지                         │
│ 전부라벨       7,571   L L …         → 표지 KV 또는 목록               │
│ 빈행           4,346                 → 버림                            │
│ 키값(복합)     2,200   L V L V …     → KV 여러 개                      │
│ 섹션제목       1,886   전체폭 L 1칸  → ### 제목                        │
│ 값1칸            424   전체폭 V 1칸  → 문단                            │
│ 키값(역순)        84   V L           → 예외⑦: 값이 먼저 온다            │
│ 전부값            71   V V …         → 표 데이터                       │
│ 라벨1칸           11                 → 제목                            │
│ 키값(꼬리라벨)     1   … L L         → 마지막 라벨이 사실은 값          │
└───────────────────────────────────────────────────────────────────────┘

────────────────────────────────────────────────────────────────────────
반드시 지켜야 하는 예외 (전수 확인)
────────────────────────────────────────────────────────────────────────
 ① XML 파서 금지 — 598개 중 189개(32%)가 XML 규격 위반이다.
      본문에 <별표3-3> 같은 꺾쇠 87파일, & 이스케이프 누락 103파일.
      html.parser 는 `<` 다음이 영어일 때만 태그로 보므로 전부 통과한다.
 ② THEAD 표 안의 TD는 라벨이 아니라 데이터 (4,404행).
      두산에너빌리티 주가표: H"일 자" 아래 L"2024/05/13" L"17,700" …
 ③ rowspan 최대 23, 한 행에 rowspan 6개까지. 대분류 스택이 여러 겹.
 ④ rowspan+colspan 동시 3,507군데 (exchange의 61배).
 ⑦ 값이 라벨보다 먼저 오는 행 84개.  U"2023년 12월 31일" L"현재기준"
 ⑧ 표 밖 <P> 산문 9,870개. 표만 보면 절반을 놓친다.
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
    para_text as _para_text,
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
           'MajorParser', 'Stats', 'corp_name_from_path', 'looks_like_major_xml']


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

    XML 파서를 쓰면 안 되는 이유는 파일 첫머리 주석 참조 (예외①).
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
        # 없던 세미콜론을 만들지 않는다 (exchange 에서 73건 깨졌던 버그)
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






def _text_no_table(node):
    """셀 안에 표가 들어있을 때, 그 표의 글자는 빼고 읽는다.

    안 그러면 20행짜리 합병일정 표가 한 줄로 뭉개진다.
    중첩 표 126개는 [_own_tables] 로 따로 뽑아 이어서 출력한다.
    """
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



class _Cell:
    __slots__ = ('text', 'tag', 'rowspan', 'colspan', 'origin_row',
                 'code', 'norm')

    def __init__(self, node, row):
        self.tag = node.tag
        self.text = flat(_text_no_table(node))   # 중첩 표 글자는 뺀다
        self.rowspan = _int(node.attrs.get('ROWSPAN'), 1)
        self.colspan = _int(node.attrs.get('COLSPAN'), 1)
        self.origin_row = row
        self.code = node.attrs.get('ACODE') or node.attrs.get('AUNIT')
        self.norm = node.attrs.get('AUNITVALUE')

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

# 1칸 표가 제목인지 판정
_RE_BRACKET_TITLE = re.compile(r'^\s*[\u3010\[]')          # 【…】 962개
_RE_NUM_TITLE = re.compile(r'^\s*\d+\s*[.\uff0e)]\s*\S')   # "11. 기타…" 359개
_RE_FOOTNOTE = re.compile(r'^\s*[\u203b*\uff0a\u4e3b(\uff08]')

# 표지의 "회 사 명 :" 같은 콜론 라벨

_PASSTHROUGH = {'BODY', 'LIBRARY', 'TABLE-GROUP', 'TBODY', 'THEAD', 'SPAN'}


class MajorParser:
    """major XML 하나를 조각(chunk) 목록으로 바꾼다.

    조각 종류
        ('h',  단계, 글자)                 제목
        ('p',  글자)                       문단
        ('kv', [키조각들], 값, 코드, 정규화값)  키-값
        ('t',  [머리글], [[행]])            표
    """

    def __init__(self, drop_empty=True, show_iso_date=True, stats=None):
        self.drop_empty = drop_empty      # 값이 '-' 인 항목 생략
        self.show_iso_date = show_iso_date  # 날짜 뒤에 (2024-09-12) 병기
        self.stats = stats

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
        self._walk(body, chunks, depth=0, ctx=[])

        # 정정공시 판정: <CORRECTION> 태그 유무.
        # manifest의 is_correction 과 598/598 일치함을 확인했다.
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
    def _walk(self, node, out, depth, ctx):
        for c in node.children:
            t = c.tag

            if t in _IGNORE:
                continue

            if t == 'CORRECTION':
                title = self._child_title(c) or '정정신고(보고)'
                out.append(('h', 2, title))
                self._walk(c, out, 2, [title])
                continue

            if t in _SECTION_TAGS:
                lvl = _SECTION_TAGS[t]
                title = self._child_title(c)
                sub = list(ctx)
                if title:
                    out.append(('h', lvl, title))
                    sub = [title]
                self._walk(c, out, lvl, sub)
                continue

            if t == 'COVER':
                title = self._child_title(c)
                if title:
                    out.append(('h', 2, title))
                self._walk(c, out, 2, [title] if title else list(ctx))
                continue

            if t in ('TITLE', 'COVER-TITLE'):
                continue                      # 위에서 이미 소비

            if t == 'TABLE':
                out.extend(self._table(c, depth))
                continue                      # 표 안으로 다시 안 들어간다

            if t == 'P':
                # 문단 융합 수정. holding/periodic 판본을 그대로 쓰면
                # major 에서는 개악이다 — <SPAN> 을 회사명·날짜에도 써서
                # '두산에너빌 리티', '(주) 셀트리온' 으로 쪼개진다(실측:
                # 삽입 285개 중 개선 36개). 그래서 앞 조각이 **소제목
                # 모양**일 때만 공백을 넣는 좁은 판본을 쓴다.
                s = clean(_para_text(c, subtitle_only=True))
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

            self._walk(c, out, depth, ctx)     # PASSTHROUGH 및 그 외

    def _child_title(self, node):
        for c in node.children:
            if c.tag in ('TITLE', 'COVER-TITLE'):
                return flat(_text(c))
        return None

    # ── 표 처리 ───────────────────────────────────────────────────────
    def _table(self, table, depth):
        # 이 표의 행만 모은다. 중첩 표(126개)의 행이 섞이면 안 된다.
        trs = [e for e in _own_nodes(table) if e.tag == 'TR']
        raw = []
        head_rows = 0
        for tr in trs:
            cells = [c for c in tr.children if c.tag in CELL_TAGS]
            if cells:
                raw.append(cells)
                if self._in_thead(tr):
                    head_rows += 1

        # 셀 안에 들어있던 표는 부모 표를 낸 뒤에 이어서 낸다.
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
        """1,538개. 제목·단위·각주·문단 네 가지 역할을 한다."""
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
        THEAD 표의 데이터 행 4,404개가 전부 TD 로 되어 있기 때문 (예외②).
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
            line = [grid[r][c].text if grid[r][c] else '' for c in keep]
            if any(x.strip() for x in line):
                rows.append(line)

        if self.stats:
            self.stats.bump(self.stats.rows, '진짜표')
        if not rows:
            return []
        return [('t', headers, rows)]

    def _live_columns(self, rows, ncol):
        """colspan 때문에 옆칸과 완전히 같은 열은 버린다.

        rows: 펼친 표의 행 목록 (각 행은 셀 또는 None 의 리스트)
        """
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
            """버퍼에 쌓인 행을 표로 낸다.

            논리 칸이 아니라 **펼친 표(grid)** 를 그대로 쓴다.
            논리 칸만 쓰면 rowspan 으로 물려받은 칸이 빠져서
            둘째 행부터 한 칸씩 밀린다.
            """
            if not buf:
                return
            keep = self._live_columns([grid[r] for r in buf], ncol)
            rows = [[grid[r][c].text if c < len(grid[r]) and grid[r][c] else ''
                     for c in keep] for r in buf]
            if len(rows) >= 2:
                out.append(('t', ['구분%d' % (i + 1) for i in range(len(keep))],
                            rows))
            else:
                out.append(('p', ' | '.join(x for x in rows[0] if x.strip())))
            buf.clear()

        for r in range(len(grid)):
            own, inherited = self._logical(grid, ncol, r)

            if not own or all(c.is_empty for c in own):
                if self.stats:
                    self.stats.bump(self.stats.rows, '빈행')
                continue

            full = (len(own) == 1 and not inherited
                    and own[0].colspan >= ncol)

            # 전체폭 라벨 1칸 → 섹션 제목
            if full and own[0].is_label:
                flush()
                out.append(('h', 4, own[0].text))
                if self.stats:
                    self.stats.bump(self.stats.rows, '섹션제목')
                continue

            # 전체폭 값 1칸 → 문단
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
                # rowspan 으로 내려온 대분류. 글자를 다시 찍지 않는다.
                if cell.is_label and not cell.is_empty:
                    inherited.append(cell)
                continue
            # 나란한 라벨 칸의 글자가 같으면 합친다 (값 칸에는 하지 않는다)
            if (own and own[-1].is_label and cell.is_label
                    and own[-1].text == cell.text):
                continue
            own.append(cell)
        return own, inherited

    def _to_kv(self, own, inherited):
        """칸들을 [라벨…][값] 런으로 잘라 키-값을 만든다.

        L V / L L V / L V L V 를 같은 규칙으로 처리하고,
        V L (값이 먼저, 84행) 도 뒤집어서 받는다.
        """
        ctx = [c.text for c in inherited]

        # 런(같은 종류 연속)으로 쪼갠다
        runs, cur = [], None
        for c in own:
            k = 'L' if c.is_label else 'V'
            if cur and cur[0] == k:
                cur[1].append(c)
            else:
                cur = [k, [c]]
                runs.append(cur)

        # 전부 라벨인 행: 표지의 "회 사 명 :" 형태만 KV로 본다
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
                for v in nxt[1]:
                    out.extend(self._emit(ctx + row_ctx, labels, v))
                matched = True
                i += 2
            elif kind == 'V' and nxt and nxt[0] == 'L':
                # 예외⑦ — 값이 먼저 온다.  U"2023년 12월 31일"  L"현재기준"
                labels = [c.text for c in nxt[1] if not c.is_empty]
                for v in cells:
                    out.extend(self._emit(ctx + row_ctx, labels, v))
                if self.stats:
                    self.stats.bump(self.stats.notes, '역순키값')
                matched = True
                i += 2
            else:
                i += 1

        if self.stats and matched:
            self.stats.bump(self.stats.rows, '키값')
        return out

    def _colon_kv(self, own, ctx):
        """표지의  L"회 사 명 :"  L"주식회사 하이브"  형태."""
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
    return _render_dsd(doc, '주요사항보고서', _iso, _esc, with_header)


# ══════════════════════════════════════════════════════════════════════
# 8. 키-값 사전 (덤) — DART 필드코드를 그대로 살린다
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
        # <COMPANY-NAME> 의 법인 등기명. 폴더명과 139건 다르다.
        # 대부분은 표기 차이(네이버(주)→NAVER)지만 진짜 사명 변경도 9건 있다.
        # (대우조선해양→한화오션 등) 마크다운에는 넣지 않고 여기에만 남긴다.
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
    """raw/major/<법인명>/<접수번호>/<접수번호>.xml 에서 법인명을 뽑는다.

    폴더명이 DART 공식 법인명이다. <COMPANY-NAME> 은 '(주)하이브' 처럼
    법인격이 붙어 있어 manifest 와 바로 안 맞는다.
    """
    import os
    return os.path.basename(os.path.dirname(os.path.dirname(path)))


def looks_like_major_xml(source):
    return '<DOCUMENT' in source[:600].upper()


def parse(source, receipt_no=None, corp_name=None, **kw):
    return MajorParser(**kw).parse(source, receipt_no, corp_name)


def parse_file(path, receipt_no=None, corp_name=None, **kw):
    import os
    source = decode(open(path, 'rb').read())
    if receipt_no is None:
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r'^\d{14}', base)
        receipt_no = m.group(0) if m else None
    if corp_name is None:
        corp_name = corp_name_from_path(path)
    return MajorParser(**kw).parse(source, receipt_no, corp_name)

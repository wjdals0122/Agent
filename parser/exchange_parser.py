# -*- coding: utf-8 -*-
"""거래소공시 HTML(A계열) → 마크다운 변환기.

대상: corpus/raw/exchange/**/*.xml  (1,469건, 전부 <html> 루트)
DSD-XML(major / holding / periodic)은 다루지 않는다.

표준 라이브러리만 쓴다. 설치할 게 없다.

────────────────────────────────────────────────────────────────────────
원본 1,469건을 전부 훑어서 확인한 사실
────────────────────────────────────────────────────────────────────────
 · 인코딩  : <meta>는 euc-kr이라고 써있지만 실제 바이트는 1469/1469 UTF-8.
             선언을 믿으면 전부 깨진다. 바이트를 직접 시험해봐야 한다.
 · 라벨/값 : <span class="xforms_input">이 있으면 값, 없으면 라벨.
             예외는 링크 셀 748건 하나뿐 (아래 예외② 참조).
 · 제목    : h1~h6, thead, th, 중첩 테이블 전부 0건.
             제목은 <span>의 굵게(bold) 스타일로만 표시된다.
 · 노이즈  : &nbsp;, 숫자 엔티티, U+00A0, 탭, 제어문자 전부 0건.
 · 행 형태 : 전체 30,525행이 아래 7종으로 끝난다. 분류 안 되는 행 0건.

     td  값셀  링크   행수    뜻
      2    1     0  19,055   기본 키-값
      3    1     0   4,343   대분류 + 소분류 + 값
      1    1     0   2,051   자유 서술 (문단)
      1    0     0   2,037   섹션 제목
      3    3     0   1,660   정정 비교표 데이터
      2    0     1     748   ※ 관련공시 링크
      3    0     0     631   정정 비교표 머리글

 · 한 행에 <td>는 최대 3개. 논리 열이 3을 넘지 않는다.
 · 값 셀 3개짜리 행 1,660개는 전부 *_Form0_RepeatTable0(정정 비교표)에만 있다.

────────────────────────────────────────────────────────────────────────
따로 처리해야 하는 예외 2가지
────────────────────────────────────────────────────────────────────────
 ① 부록 제목 58건
    【공시유보 관련사항】(51) / 【계약상 특이사항 등 투자위험요소】(14)
    → 굵게(bold)는 있는데 가운데정렬(center)이 없다. 일반 제목 규칙으로는
      못 잡는다. 놓치면 부록 표의 내용이 앞 섹션에 잘못 붙는다.
    → _is_heading_span() 2순위 규칙

 ② ※ 관련공시 링크 셀 748건
    xforms_input이 없어서 라벨로 보이지만 실제로는 값이다.
    (링크 든 <td> 748개 중 xforms_input 가진 것 0개)
    → 놓치면 URL이 통째로 사라진다.
    → _read_cell() 의 <a href> 예외

 나머지 예외(정정블록 위치가 앞뒤로 갈림 660건 / 제목 개수 1~2개 /
 테이블 개수 1~5개 / rowspan+colspan 동시 57건)는 별도 분기 없이
 일반 규칙으로 흡수된다.
"""

import re
from html.entities import html5 as _HTML5_ENTITIES
from html.parser import HTMLParser

__all__ = ['parse_file', 'parse', 'to_markdown', 'to_dict', 'decode', 'Stats']


# ══════════════════════════════════════════════════════════════════════
# 1. 인코딩 — 문서에 적힌 charset을 믿지 않는다
# ══════════════════════════════════════════════════════════════════════

def decode(raw_bytes):
    """바이트를 직접 시험해서 글자로 바꾼다.

    A계열은 전부 UTF-8이지만 <meta>에는 euc-kr이라고 적혀 있다.
    선언을 읽지 말고 UTF-8로 먼저 시도한 뒤, 실패하면 cp949로 넘어간다.
    """
    if raw_bytes[:3] == b'\xef\xbb\xbf':          # UTF-8 BOM
        raw_bytes = raw_bytes[3:]
    try:
        return raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return raw_bytes.decode('cp949', errors='replace')


# ══════════════════════════════════════════════════════════════════════
# 2. 글자 청소
# ══════════════════════════════════════════════════════════════════════

# <br>은 속성 이름이 두 가지로 갈린다.
#   <br xmlns:java="...">  1,467건
#   <br java="...">          369건
# 속성을 고정으로 잡으면 369건이 샌다.
_RE_BR = re.compile(r'<br\b[^>]*/?>', re.I)

# <!--?javax.xml.transform.disable-output-escaping?--> 같은 찌꺼기
_RE_COMMENT = re.compile(r'<!--.*?-->', re.S)

# <a href="주소">글자</a>  →  [글자](주소)
_RE_ANCHOR = re.compile(
    r'<a\b[^>]*?href\s*=\s*["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)

_RE_TAG = re.compile(r'<[^>]+>')
_RE_MULTISPACE = re.compile(r'[ \t\u00a0\u3000]{2,}')
_RE_INVISIBLE = re.compile(r'[\u200b-\u200d\ufeff\x00-\x08\x0b\x0c\x0e-\x1f]')

# 사실상 빈 값. '해당사항없음' / '미해당' / '해당' / '아니오'는
# 진짜 의미가 있는 답이므로 여기 넣으면 안 된다.
_EMPTY_VALUES = {'-', '\u2013', '\u2014', '', '.', '\u00b7'}

_ENTITIES = [('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'),
             ('&quot;', '"'), ('&apos;', "'"), ('&amp;', '&')]


def _decode_entities(s):
    if '&' not in s:
        return s
    s = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
    for src, dst in _ENTITIES:      # &amp;는 반드시 마지막
        s = s.replace(src, dst)
    return s


def _normalize_lines(s):
    """줄 단위로 공백을 정리한다.

    전체를 한꺼번에 정리하면 안 된다. 예를 들어

        - 상기 2. 계약금액은 원화 환산액임.
            PKG#1 : 약 USD 167백만
            PKG#4 : 약 USD 509백만

    처럼 들여쓰기로 단계를 표현한 글이 한 덩어리로 뭉개진다.
    """
    out = []
    for line in s.split('\n'):
        line = _RE_MULTISPACE.sub(' ', line).strip()
        if line:
            out.append(line)
    return '\n'.join(out)


def clean(raw_html):
    """HTML 조각 → 깨끗한 글자.

    순서가 중요하다. 링크와 <br>을 태그 지우기보다 **먼저** 처리해야
    주소와 줄바꿈이 살아남는다.
    """
    s = _RE_COMMENT.sub(' ', raw_html)                      # ① 주석

    def _anchor(m):                                          # ② 링크
        url = _decode_entities(m.group(1).strip())
        text = _decode_entities(
            _RE_TAG.sub('', _RE_BR.sub(' ', m.group(2)))).strip()
        if not url:
            return text
        return url if not text else '[%s](%s)' % (text, url)

    s = _RE_ANCHOR.sub(_anchor, s)
    s = _RE_BR.sub('\n', s)                                  # ③ 줄바꿈
    s = _RE_TAG.sub('', s)                                   # ④ 나머지 태그
    s = _decode_entities(s)                                  # ⑤ 엔티티
    s = s.replace('\u00a0', ' ').replace('\u3000', ' ')      # ⑥ 특수 공백
    s = _RE_INVISIBLE.sub('', s)
    return _normalize_lines(s)                               # ⑦ 줄 정리


def is_empty_value(s):
    return s.strip() in _EMPTY_VALUES


# ══════════════════════════════════════════════════════════════════════
# 3. 아주 얕은 HTML 트리 (필요한 것만)
# ══════════════════════════════════════════════════════════════════════

class _Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent', 'raw')

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children = []
        self.raw = []          # 자식 노드 + 글자 조각이 순서대로 섞여 있음


_VOID_TAGS = {'br', 'meta', 'img', 'link', 'hr', 'input', 'col'}


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = _Node('#root', {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            self.cur.raw.append(self.get_starttag_text() or '')
            return
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        self.cur.raw.append(node)
        self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.raw.append(self.get_starttag_text() or '')

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent          # 안 닫힌 태그 방어
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.raw.append(data)

    def handle_entityref(self, name):
        # html.parser 는 `&` 뒤에 알파벳이 오면 특수문자 시작으로 본다.
        # 원본에 세미콜론이 있었는지는 알려주지 않는다.
        #
        # 무조건 `&이름;` 으로 되돌리면 **없던 세미콜론을 만들어낸다.**
        #   원본 `삼성E&A`  →  `삼성E&A;`   ← 실제로 73건에서 이렇게 깨졌다
        #
        # 그래서 진짜 HTML 특수문자 이름일 때만 세미콜론을 붙이고,
        # 아니면 `&이름` 그대로 둔다.
        if (name + ';') in _HTML5_ENTITIES or name in _HTML5_ENTITIES:
            self.cur.raw.append('&%s;' % name)
        else:
            self.cur.raw.append('&%s' % name)

    def handle_charref(self, name):
        self.cur.raw.append('&#%s;' % name)

    def handle_comment(self, data):
        self.cur.raw.append('<!--%s-->' % data)


def _inner_html(node):
    parts = []
    for item in node.raw:
        if isinstance(item, _Node):
            attrs = ''.join(' %s="%s"' % (k, v) for k, v in item.attrs.items())
            parts.append('<%s%s>%s</%s>'
                         % (item.tag, attrs, _inner_html(item), item.tag))
        else:
            parts.append(item)
    return ''.join(parts)


def _descendants(node):
    for child in node.children:
        yield child
        for g in _descendants(child):
            yield g


def _find(node, tag, cond=None):
    for d in _descendants(node):
        if d.tag == tag and (cond is None or cond(d)):
            return d
    return None


# ══════════════════════════════════════════════════════════════════════
# 4. 표 한 칸
# ══════════════════════════════════════════════════════════════════════

class _Cell:
    __slots__ = ('text', 'is_label', 'rowspan', 'colspan', 'origin_row')

    def __init__(self, text, is_label, rowspan, colspan, origin_row):
        self.text = text
        self.is_label = is_label      # 라벨(키)인가? False면 값
        self.rowspan = rowspan
        self.colspan = colspan
        self.origin_row = origin_row  # 이 칸이 처음 나온 행 번호

    @property
    def is_empty(self):
        return not self.text.strip()


# ══════════════════════════════════════════════════════════════════════
# 5. 통계 (검증용, 없어도 동작함)
# ══════════════════════════════════════════════════════════════════════

class Stats:
    """행이 어떤 모양으로 분류됐는지 세어둔다. 원본 통계와 비교해 검증한다."""

    def __init__(self):
        self.headings = {}
        self.shapes = {}
        self.notes = {}
        self.matrix_tables = {}

    def _bump(self, box, key):
        box[key] = box.get(key, 0) + 1

    def as_dict(self):
        return {
            '제목': self.headings,
            '행모양': self.shapes,
            '참고': self.notes,
            '비교표': self.matrix_tables,
        }


# ══════════════════════════════════════════════════════════════════════
# 6. 파서
# ══════════════════════════════════════════════════════════════════════

# ※ 【 [ 1. 처럼 스스로 독립된 항목임을 알리는 머리표.
# 이런 라벨은 위쪽 rowspan 대분류를 물려받지 않는다.
_RE_STANDALONE = re.compile(r'^\s*(\u203b|\uff0a|\*|\u3010|\[|\d+\s*[.\uff0e)]\s)')

# 부록 제목: 【 로 시작
_RE_APPENDIX = re.compile(r'^\s*[\u3010\[]')


class ExchangeParser:
    """거래소공시 HTML 하나를 조각(chunk) 목록으로 바꾼다.

    조각 종류
        ('h',  단계, 글자)                 제목
        ('p',  글자)                       문단
        ('kv', [키조각들], 값, [섹션])      키-값
        ('t',  [머리글], [[행]], [섹션])    표
    """

    def __init__(self, drop_empty=True, merge_duplicate_labels=True,
                 guard_fake_context=True, stats=None):
        # 값이 '-' 인 항목을 아예 빼버린다. 원본을 다 남기려면 False.
        self.drop_empty = drop_empty
        # 나란히 붙은 라벨 칸의 글자가 같으면 하나로 합친다.
        self.merge_duplicate_labels = merge_duplicate_labels
        # 가짜 계층을 만드는 rowspan 물려받기를 막는다.
        self.guard_fake_context = guard_fake_context
        self.stats = stats

    # ── 진입점 ────────────────────────────────────────────────────────
    def parse(self, source, receipt_no=None, corp_name=None):
        """corp_name: 폴더명(=DART 공식 법인명). 넘기면 '회사명'으로 쓰고,
        <title>에서 뽑은 이름은 '공시당시_회사명'으로 따로 남긴다.

        <title>의 회사명은 **공시한 시점의 사명**이라 지금과 다를 수 있다.
        (확인된 48건: 삼성엔지니어링→삼성E&A 37, 현대중공업→HD현대중공업 4,
         대우조선해양→한화오션 4, OCI→OCI홀딩스, 현대일렉트릭→HD현대일렉트릭,
         JYP Ent.→JYP Ent)

        둘 다 쓸모가 있다.
          회사명        = 지금 이름. 다른 자료와 연결할 때 쓰는 열쇠.
          공시당시_회사명 = 그때 이름. 공시 시점의 사실 기록.
        """
        builder = _TreeBuilder()
        builder.feed(source)
        root = builder.root

        # <title> = "회사명/공시유형/(날짜)공시유형"
        title_node = _find(root, 'title')
        raw_title = clean(_inner_html(title_node)) if title_node else ''
        parts = [p.strip() for p in raw_title.split('/')]

        self._remove_noise(root)

        container = _find(root, 'div',
                          lambda d: 'xforms' in d.attrs.get('class', '').split())
        body = container or _find(root, 'body') or root

        chunks = []
        section = []
        self._walk(body, chunks, section)

        # 정정공시인가?
        #
        # div[id^=LIB_] 존재로 판단하면 안 된다. 그 상자는 정정 블록과
        # 부록 블록이 같이 쓰기 때문에 660건이 잡혀 29건이 부풀려진다.
        # '정정신고(보고)' 제목이 정확한 신호다.
        # (확인: 이 제목 631건 = 비교표 631건, 완전히 일치)
        is_correction = any(c[0] == 'h' and c[2].startswith('정정신고')
                            for c in chunks)

        title_corp = parts[0] if parts and parts[0] else None
        return {
            '회사명': corp_name or title_corp,
            '공시당시_회사명': title_corp,
            '사명변경': bool(corp_name and title_corp and corp_name != title_corp),
            '공시유형': parts[1] if len(parts) > 1 and parts[1] else None,
            '접수번호': receipt_no,
            '정정공시': is_correction,
            'chunks': chunks,
        }

    # ── 노이즈 선제거 ─────────────────────────────────────────────────
    def _remove_noise(self, node):
        keep = []
        for child in node.children:
            if child.tag in ('style', 'script'):
                continue
            # .noprint 더미 span (1469/1469 전부에 있음)
            if 'display:none' in child.attrs.get('style', '').replace(' ', ''):
                continue
            self._remove_noise(child)
            keep.append(child)
        node.children = keep
        node.raw = [r for r in node.raw
                    if (not isinstance(r, _Node)) or (r in keep)]

    # ── 문서 훑기 ─────────────────────────────────────────────────────
    def _walk(self, node, out, section):
        for child in node.children:
            if child.tag == 'table':
                out.extend(self._read_table(child, section))
                continue                     # 표 안으로 다시 안 들어간다

            if child.tag == 'span':
                text = clean(_inner_html(child))
                if self._is_heading_span(child, text):
                    out.append(('h', 2, text))
                    section[:] = [text]
                    if self.stats:
                        style = child.attrs.get('style', '').replace(' ', '')
                        self.stats._bump(
                            self.stats.headings,
                            '본문제목' if 'text-align:center' in style else '부록제목')
                    continue

            self._walk(child, out, section)

    def _is_heading_span(self, node, text):
        """제목 <span> 인지 판단한다. 규칙 2단계.

        1순위  굵게 + 가운데정렬  → 본문 제목
               (div.xforms_title 안 981건 + 클래스 없는 것 488건을 한 규칙으로)
        2순위  굵게 + 글자가 【 로 시작  → 부록 제목  (예외①, 58건)
               이 span들은 가운데정렬이 없어서 1순위에 안 걸린다.
        """
        if not text:
            return False
        style = node.attrs.get('style', '').replace(' ', '')
        if 'font-weight:bold' not in style:
            return False
        if 'text-align:center' in style:
            return True
        return bool(_RE_APPENDIX.match(text))

    # ── 표 읽기 ───────────────────────────────────────────────────────
    def _read_table(self, table, section):
        table_id = table.attrs.get('id', '')
        rows = []
        for tr in [d for d in _descendants(table) if d.tag == 'tr']:
            cells = [self._read_cell(td) for td in tr.children
                     if td.tag in ('td', 'th')]
            if cells:
                rows.append(cells)
        grid, ncol = self._expand(rows)
        return self._classify(grid, ncol, table_id, section)

    def _read_cell(self, td):
        span = _find(td, 'span')
        is_value = (span is not None
                    and 'xforms_input' in span.attrs.get('class', '').split())

        # 예외② : xforms_input이 없어도 링크가 들어있으면 값이다.
        # ※ 관련공시 셀 748건이 여기 해당한다. 놓치면 주소가 사라진다.
        if not is_value and _find(td, 'a', lambda a: 'href' in a.attrs):
            is_value = True

        return (clean(_inner_html(td)),
                not is_value,
                int(td.attrs.get('rowspan', 1) or 1),
                int(td.attrs.get('colspan', 1) or 1))

    def _expand(self, raw_rows):
        """rowspan / colspan을 펼쳐서 빈틈 없는 2차원 표로 만든다.

        pending[열] = (칸, 남은 행 수) 를 들고 다니면서 채운다.
        """
        grid = []
        pending = {}
        max_cols = 0

        for row_no, source in enumerate(raw_rows):
            row = []
            col = 0
            i = 0

            def put(index, cell):
                while len(row) <= index:
                    row.append(None)
                row[index] = cell

            while True:
                waiting = pending.get(col)
                if waiting:
                    cell, left = waiting
                    for c in range(cell.colspan):
                        put(col + c, cell)
                    key = col
                    col += cell.colspan
                    left -= 1
                    if left <= 0:
                        del pending[key]
                    else:
                        pending[key] = (cell, left)
                    continue

                if i >= len(source):
                    break

                text, is_label, rowspan, colspan = source[i]
                i += 1
                cell = _Cell(text, is_label, rowspan, colspan, row_no)
                for c in range(colspan):
                    put(col + c, cell)
                if rowspan > 1:
                    pending[col] = (cell, rowspan - 1)
                col += colspan

            max_cols = max(max_cols, len(row))
            grid.append(row)

        for row in grid:
            while len(row) < max_cols:
                row.append(None)
        return grid, max_cols

    def _to_logical(self, grid, ncol):
        """펼친 표를 '뜻이 있는 칸'만 남긴 행 목록으로 줄인다.

        결과: [(이 행이 새로 만든 칸들, 위에서 물려받은 대분류들), ...]
        """
        result = []
        for row_no, physical in enumerate(grid):
            own = []
            inherited = []
            previous = None

            for col in range(ncol):
                cell = physical[col] if col < len(physical) else None
                if cell is None or cell is previous:
                    continue                       # colspan 중복 제거
                previous = cell

                if cell.origin_row != row_no:
                    # rowspan으로 내려온 칸 → 대분류 후보.
                    # 글자를 다시 찍지 않는다. (세로 중복 방지)
                    if cell.is_label and not cell.is_empty:
                        inherited.append(cell)
                    continue

                # 나란히 붙은 칸의 글자가 같으면 합친다.
                #
                # ⚠️ 라벨 칸에만 적용한다. 값 칸까지 합치면
                # 정정 비교표의 "| 2. 계약내역 | - | - |" 같은 행이
                # 3칸 → 2칸으로 줄어서 표가 끊긴다.
                # (실제로 이 실수 때문에 비교표 631개 중 564개만 잡혔었다)
                if (self.merge_duplicate_labels and own
                        and own[-1].is_label and cell.is_label
                        and own[-1].text == cell.text):
                    continue

                own.append(cell)

            result.append((own, inherited))
        return result

    # ── 행 분류 (핵심) ────────────────────────────────────────────────
    def _classify(self, grid, ncol, table_id, section):
        """표를 조각으로 바꾼다.

        **표 단위가 아니라 '행 묶음' 단위로 판단한다.**
        정정공시 표는 하나의 <table> 안에
            키-값 행 → "4. 정정사항" 섹션 행 → 비교표 머리글 → 데이터 행
        이 순서로 같이 들어있다 (631건).
        "이 표는 키-값표 / 저 표는 진짜표" 식으로 나누면 여기서 다 깨진다.
        """
        out = []
        rows = self._to_logical(grid, ncol)
        local_section = list(section)
        i = 0

        while i < len(rows):
            own, inherited = rows[i]

            if not own or all(c.is_empty for c in own):
                i += 1
                continue

            # 비교표 (머리글 + 데이터 행)
            found = self._try_matrix(rows, i)
            if found:
                headers, data, next_i = found
                out.append(('t', headers, data, list(local_section)))
                if self.stats:
                    self.stats._bump(self.stats.matrix_tables,
                                     re.sub(r'^XForm\w+?_', '', table_id))
                i = next_i
                continue

            is_full_width = (len(own) == 1 and not inherited
                             and own[0].colspan >= ncol)

            # 섹션 제목 (한 칸이 표 전체 폭을 차지하는 라벨)
            if is_full_width and own[0].is_label:
                out.append(('h', 3, own[0].text))
                local_section = section + [own[0].text]
                if self.stats:
                    self.stats._bump(self.stats.shapes, '섹션제목')
                i += 1
                continue

            # 자유 서술 (한 칸이 표 전체 폭을 차지하는 값)
            if is_full_width:
                if not (self.drop_empty and is_empty_value(own[0].text)):
                    out.append(('p', own[0].text))
                if self.stats:
                    self.stats._bump(self.stats.shapes, '문단')
                i += 1
                continue

            # 키-값
            out.extend(self._to_key_values(own, inherited, local_section))
            i += 1

        return out

    def _try_matrix(self, rows, start):
        """머리글 행(전부 라벨, 3칸 이상) + 뒤따르는 데이터 행(전부 값)."""
        own, _ = rows[start]
        width = len(own)
        if width < 3 or not all(c.is_label for c in own):
            return None

        data = []
        i = start + 1
        while i < len(rows):
            next_own, _ = rows[i]
            if len(next_own) != width:
                break
            if not all(not c.is_label for c in next_own):
                break
            data.append([c.text for c in next_own])
            i += 1

        if not data:
            return None

        headers = [(c.text.strip() or '구분') for c in own]
        return headers, data, i

    def _to_key_values(self, own, inherited, section):
        """칸들을 [라벨...][값] 묶음으로 잘라 키-값을 만든다."""
        out = []
        context = [c.text for c in inherited]

        # 가짜 계층 막기.
        # 고려아연 20250120800751 같은 경우
        #     [6. 기타 투자판단…(rowspan=2 colspan=2)][내용]
        #     [※ 관련공시][-]
        # 여기서 rowspan은 그냥 칸 높이를 맞추려는 것이지 계층이 아니다.
        # 그대로 물려받으면 "6. 기타 투자판단… > ※ 관련공시"라는
        # 없는 계층이 생긴다.
        if (self.guard_fake_context and own and own[0].is_label
                and _RE_STANDALONE.match(own[0].text)):
            if context and self.stats:
                self.stats._bump(self.stats.notes, 'rowspan_물려받기_차단')
            context = []

        labels = []
        # 첫 라벨 묶음이 2개 이상("L L V…")이면 마지막 하나만 소분류로 보고
        # 앞쪽은 이 행 전체의 대분류로 승격해 뒤따르는 라벨-값 쌍에도
        # 물려준다(row_ctx). 라벨을 비우는 대신 row_ctx로 되돌린다.
        # ⚠️ 라벨 두 개가 대등한 열(예: "구분"/"항목")인 표에도 이 규칙을
        # 적용하면 없는 계층이 생길 수 있다 — 지금까지는 항상 대분류/
        # 소분류 관계였다.
        row_ctx = []
        first_group_seen = False
        last_was_value = False
        value_count = 0

        for cell in own:
            if cell.is_label:
                if last_was_value:
                    labels = list(row_ctx)
                    last_was_value = False
                    value_count = 0
                if not cell.is_empty:
                    labels.append(cell.text)
            else:
                if not first_group_seen:
                    first_group_seen = True
                    if len(labels) >= 2:
                        row_ctx = labels[:-1]
                value_count += 1
                text = cell.text
                skip = self.drop_empty and is_empty_value(text)
                if not skip and (labels or context):
                    key = context + labels
                    if value_count > 1:          # 한 라벨에 값이 여러 개
                        key = key + ['[%d]' % value_count]
                    out.append(('kv', key,
                                None if is_empty_value(text) else text,
                                list(section)))
                last_was_value = True

        if self.stats:
            if not out and labels:
                self.stats._bump(
                    self.stats.notes,
                    '빈값이라_생략' if value_count else '값칸_없음_이상')
            self.stats._bump(self.stats.shapes,
                             '키값_물려받음%d_칸%d' % (len(inherited), len(own)))
        return out


# ══════════════════════════════════════════════════════════════════════
# 7. 마크다운으로 쓰기
# ══════════════════════════════════════════════════════════════════════

def _escape_cell(s):
    return s.replace('|', '\\|').replace('\n', '<br>').strip()


def to_markdown(doc, with_header=True):
    lines = []

    if with_header:
        lines.append('# %s' % (doc['공시유형'] or '거래소공시'))
        meta = [x for x in [
            doc['회사명'],
            ('접수번호 %s' % doc['접수번호']) if doc['접수번호'] else None,
            '정정공시' if doc['정정공시'] else None,
            # 사명이 바뀐 회사는 공시 당시 이름도 남긴다.
            ('공시 당시 사명: %s' % doc['공시당시_회사명'])
            if doc.get('사명변경') else None,
        ] if x]
        if meta:
            lines.append('> ' + ' · '.join(meta))
        lines.append('')

    for chunk in doc['chunks']:
        kind = chunk[0]

        if kind == 'h':
            level = min(max(chunk[1], 1), 6)
            lines.append('')
            lines.append('#' * level + ' ' + chunk[2])
            lines.append('')

        elif kind == 'p':
            lines.append(chunk[1])
            lines.append('')

        elif kind == 'kv':
            key = ' > '.join(chunk[1])
            value = chunk[2]
            if value is None:
                lines.append('- **%s**: _(해당없음)_' % key)
            elif '\n' in value:
                # 여러 줄 값은 들여써야 목록 구조가 안 깨진다
                parts = value.split('\n')
                lines.append('- **%s**: %s' % (key, parts[0]))
                for p in parts[1:]:
                    lines.append('  ' + p)
            else:
                lines.append('- **%s**: %s' % (key, value))

        elif kind == 't':
            headers, rows = chunk[1], chunk[2]
            n = len(headers)
            lines.append('')
            lines.append('| ' + ' | '.join(_escape_cell(h) for h in headers) + ' |')
            lines.append('| ' + ' | '.join('---' for _ in headers) + ' |')
            for row in rows:
                cells = [_escape_cell(c) for c in row] + [''] * (n - len(row))
                lines.append('| ' + ' | '.join(cells[:n]) + ' |')
            lines.append('')

    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'


# ══════════════════════════════════════════════════════════════════════
# 8. 키-값 사전으로 쓰기 (덤)
# ══════════════════════════════════════════════════════════════════════

def to_dict(doc):
    fields = {}
    tables = []
    texts = []
    seen = {}

    for chunk in doc['chunks']:
        kind = chunk[0]
        if kind == 'kv':
            key = ' > '.join(chunk[3] + chunk[1])
            n = seen.get(key, 0) + 1
            seen[key] = n
            if n > 1:
                key = '%s#%d' % (key, n)
            fields[key] = chunk[2]
        elif kind == 't':
            headers, rows = chunk[1], chunk[2]
            tables.append({
                '섹션': chunk[3],
                '머리글': headers,
                '행': [dict(zip(headers, r)) for r in rows],
            })
        elif kind == 'p':
            texts.append(chunk[1])

    return {
        '회사명': doc['회사명'],
        '공시당시_회사명': doc.get('공시당시_회사명'),
        '사명변경': doc.get('사명변경', False),
        '공시유형': doc['공시유형'],
        '접수번호': doc['접수번호'],
        '정정공시': doc['정정공시'],
        '항목': fields,
        '표': tables,
        '본문': texts,
    }


# ══════════════════════════════════════════════════════════════════════
# 9. 편의 함수
# ══════════════════════════════════════════════════════════════════════

def parse(source, receipt_no=None, corp_name=None, **kwargs):
    return ExchangeParser(**kwargs).parse(source, receipt_no, corp_name)


def corp_name_from_path(path):
    """raw/exchange/<법인명>/<접수번호>/<접수번호>.xml 에서 법인명을 뽑는다.

    폴더명은 DART 공식 법인명이라 manifest 와 1,469건 전부 일치한다.
    <title> 의 회사명(공시 당시 사명)보다 이쪽이 정확하다.
    """
    import os
    return os.path.basename(os.path.dirname(os.path.dirname(path)))


def parse_file(path, receipt_no=None, corp_name=None, **kwargs):
    import os
    with open(path, 'rb') as f:
        source = decode(f.read())
    if receipt_no is None:
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r'^\d{14}', base)
        receipt_no = m.group(0) if m else None
    if corp_name is None:
        corp_name = corp_name_from_path(path)
    return ExchangeParser(**kwargs).parse(source, receipt_no, corp_name)


def looks_like_exchange_html(source):
    """A계열(거래소공시 HTML)인지 본다. DSD-XML이면 False."""
    return '<html' in source[:400].lower()

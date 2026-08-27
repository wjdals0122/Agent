# -*- coding: utf-8 -*-
"""PDF로 대체수집된 periodic 문서(KB금융/한화오션/한화에어로스페이스)를
periodic_parser.py가 만드는 것과 같은 형식의 마크다운으로 바꾼다.

DART가 이 3건은 XML을 안 줘서(status 014) 대신 공식 PDF + 뷰어HTML로
받았다(3_preprocess/pdf_parser.py 참고). pdf_parser.parse_pdf_document()가
heading/paragraph/table Block 목록을 뽑아주는데, 이 모듈은 그 Block들을
periodic_parser.py의 chunk 형식(('h',...), ('p',...), ('kv',...), ('t',...))
으로 바꿔서 periodic_parser.to_markdown()을 그대로 재사용한다 — 그래서
표지 라벨:값, ISO 날짜 병기, "L L V" 대분류 승격 같은 규칙이 XML 문서와
동일하게 적용된다.

한계 (PDF에는 XML의 TD/TE/TU/TH 태그, THEAD, USERMARK 음영 같은 신호가
전혀 없다):
  - 표 안 라벨/값 구분: 날짜·숫자처럼 "값처럼 생긴" 글자만 값으로 보고
    나머지는 라벨로 본다(_classify_cell_text). 표 전체가 진짜 다열
    데이터표(재무제표 등)인지 라벨:값 목록인지는 열 수/행 수로 가른다
    (_classify_table) — 실제 3건을 보면 열≤2 이거나 행≤3인 표는 거의
    항상 라벨:값 목록이고("구분/당기(당분기,당반기)" 같은 정정 안내
    표), 그보다 크면 재무제표류 실데이터표(매출 breakdown 등)였다.
  - 표지 인적사항(회사명/대표이사/본점소재지 등)은 표가 아니라 문단
    하나로 통째로 뽑혀 나온다(선/사각형이 없어 pdfplumber가 표로 못
    잡음). DART 표지 정형 문구를 정규식으로 인식해 키-값으로 쪼갠다
    (_extract_cover_kv). 매칭에 실패하면 원래 문단을 그대로 둔다 —
    억지로 잘못 쪼개는 것보다 안전한 폴백이다.
"""

import os
import re
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_PREPROCESS_DIR = os.path.join(_REPO_ROOT, '3_preprocess')
for _p in (_PREPROCESS_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pdf_parser  # 3_preprocess/pdf_parser.py
import periodic_parser as pp


# ══════════════════════════════════════════════════════════════════════
# 1. 표 셀 라벨/값 추정
# ══════════════════════════════════════════════════════════════════════

_RE_KDATE = re.compile(r'^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일$')
# "2023.04.27" 같은 점 구분 날짜 — 배당/이사회 표 등에서 "YYYY년 MM월
# DD일" 대신 흔히 쓴다. 일자가 없는 "2023.03"(=2023년 1분기) 꼴도 있다.
_RE_DOT_DATE = re.compile(r'^(\d{4})\.(\d{1,2})\.(\d{1,2})$')
_RE_DOT_YM = re.compile(r'^(\d{4})\.(\d{1,2})$')
# 재무제표 음수 표기: 괄호(1,234) 와 △/▲/▽ 접두(△1,234) 둘 다 흔하다.
# 이걸 안 잡으면 음수로 가득한 데이터 행이 값(V) 칸 없이 전부 라벨로
# 보여 _table_looks_like_kv() 가 다열 데이터표를 놓칠 수 있다.
_RE_NUMERIC = re.compile(r'^[△▲▽]?\(?[+\-]?[\d,]+(\.\d+)?%?\)?$')
# 찬반/여부를 O·X 한 글자로 표시하는 표(배당 확정 여부, 이사회 찬반 등).
_MARK_VALUES = {'O', 'X', 'o', 'x', '○', '●', '◯', '×', 'Ο', 'Χ', '√', '✓'}


def _classify_cell_text(text):
    """(is_label, norm)을 돌려준다. norm은 ISO 날짜 병기용(AUNITVALUE 대응).

    '-'/'--'는 DART 표에서 "해당없음" 값 자리표시자다(XML에서도 TU/TE
    같은 값 칸에 들어간다) — 라벨로 보면 안 된다. 라벨로 잘못 보면 숫자로
    가득한 데이터 행이 "라벨, 라벨, 값, 값, 값"처럼 쪼개져 값 런이 실제보다
    짧게 잡히고, _table_looks_like_kv()가 다열 데이터표를 못 잡아낸다.

    O/X 같은 찬반 표시나 "2023.04.27" 같은 점 구분 날짜도 같은 이유로
    값으로 봐야 한다 — 실측(KB금융 배당 이력표)에서 이걸 라벨로 잘못 보면
    "구분 | 결산월 | 배당여부 | 배당액확정일 | 배당기준일 | ... | 비고"
    7열 표의 한 행이 "라벨,값,라벨(4개),값"으로 쪼개지고, _to_kv()가
    마지막 값 칸을 빈 값('-')으로 보고 통째로 버려서 배당여부/확정일/
    기준일/비고 네 칸이 결과에서 완전히 사라졌다.
    """
    t = text.strip()
    if not t:
        return True, None
    if t in ('-', '--') or t in _MARK_VALUES:
        return False, None
    m = _RE_KDATE.match(t)
    if m:
        y, mo, d = m.groups()
        return False, '%04d%02d%02d' % (int(y), int(mo), int(d))
    m = _RE_DOT_DATE.match(t)
    if m:
        y, mo, d = m.groups()
        return False, '%04d%02d%02d' % (int(y), int(mo), int(d))
    if _RE_DOT_YM.match(t):
        return False, None
    if _RE_NUMERIC.match(t):
        return False, None
    return True, None


class _PdfCell:
    """periodic_parser._Cell과 같은 인터페이스(text/rowspan/colspan/
    origin_row/code/norm/is_header/is_label/is_empty)를 갖는 값 객체.
    PDF table Block의 TableCell로부터 만든다.
    """
    __slots__ = ('text', 'rowspan', 'colspan', 'origin_row', 'code',
                 'norm', 'is_header', 'is_label')

    def __init__(self, tc):
        self.text = (tc.text or '').strip()
        self.rowspan = max(tc.rowspan or 1, 1)
        self.colspan = max(tc.colspan or 1, 1)
        self.origin_row = tc.row
        self.code = None
        self.is_header = False
        self.is_label, self.norm = _classify_cell_text(self.text)

    @property
    def is_empty(self):
        return not self.text.strip()


def _build_grid(cells, n_rows, n_cols):
    """TableCell 목록(row/col/rowspan/colspan)을 periodic_parser._expand()가
    만드는 것과 같은 2차원 grid(칸 겹침은 같은 객체 참조로 표시)로 편다.
    """
    grid = [[None] * n_cols for _ in range(n_rows)]
    for tc in cells:
        pc = _PdfCell(tc)
        for dr in range(pc.rowspan):
            for dc in range(pc.colspan):
                r, c = tc.row + dr, tc.col + dc
                if r < n_rows and c < n_cols:
                    grid[r][c] = pc
    return grid


# ══════════════════════════════════════════════════════════════════════
# 2. 표 하나를 chunk 목록으로
# ══════════════════════════════════════════════════════════════════════

def _kv_table_from_grid(parser, grid, ncol):
    """periodic_parser.PeriodicParser._kv_table()과 같은 몸통이지만,
    XML raw 대신 이미 만들어둔 grid를 받는다(_expand()를 안 거친다).
    """
    out, buf = [], []

    def flush():
        if buf:
            out.extend(parser._flush_buffer(grid, ncol, buf))
            buf.clear()

    for r in range(len(grid)):
        own, inherited = parser._logical(grid, ncol, r)
        if not own or all(c.is_empty for c in own):
            continue

        full = (len(own) == 1 and not inherited and own[0].colspan >= ncol)

        if full and own[0].is_label:
            flush()
            out.append(('h', 4, own[0].text))
            continue
        if full:
            flush()
            if not (parser.drop_empty and pp.is_empty_value(own[0].text)):
                out.append(('p', own[0].text))
            continue

        kvs = parser._to_kv(own, inherited)
        if kvs:
            flush()
            out.extend(kvs)
        else:
            buf.append(r)

    flush()
    return out


# 표 머리글에 자주 나오는 "값처럼 생겼지만 실은 라벨"인 칸 — 연도만 있는
# 열머리글("2020", "2024년" 등), 몇 기인지("제 25 기", "제25기 1분기").
# is_label 판정(_classify_cell_text)에는 안 쓴다 — 거긴 "이 칸이 값이냐"만
# 보면 되고, 여기는 "표 머리글로 쓸 만하냐"라는 별개의 질문이다.
_RE_BARE_YEAR = re.compile(r'^(19|20)\d{2}\s*년?\s*(\d{1,2}\s*월)?$')
_RE_FISCAL_TERM = re.compile(r'^제\s*\d+\s*기')


def _looks_like_header_cell(cell):
    return (cell.is_label or bool(_RE_BARE_YEAR.match(cell.text))
            or bool(_RE_FISCAL_TERM.match(cell.text)))


def _table_looks_like_kv(grid, ncol):
    """이 표의 모든 행에서, 값(V) 칸이 옆칸과 이어져 2개 이상 붙는 경우가
    한 번도 없으면 "라벨:값 목록"으로 안전하게 본다. 하나라도 있으면
    다열 데이터표(재무제표 등)로 보고 표 전체를 real-table로 낸다.

    이 확인이 없으면 "구분 | 2020 | 2021 | 2022 | 2023" 같은 표가
    kv 경로로 들어가 _to_kv()가 값 런(2020~2023)을 전부 같은 라벨
    "구분"에 붙여버린다 — 어느 값이 어느 열(연도)인지 정보가 사라지고,
    "- **구분**: 2020" "- **구분**: 2021" 처럼 같은 키가 반복되면서
    서로 다른 값처럼 보이는(실은 서로 다른 열이었던) 결과가 나온다.
    """
    for row in grid:
        prev, run_is_v, run_len = None, None, 0
        for c in row[:ncol]:
            if c is None or c is prev:
                continue
            prev = c
            is_v = not c.is_label
            if is_v and run_is_v:
                run_len += 1
                if run_len >= 2:
                    return False
            else:
                run_is_v, run_len = is_v, 1
    return True


def _real_table_from_grid(parser, grid, ncol):
    """다열 데이터표(재무제표 등)로 보고 그대로 표로 낸다. 첫 행이 전부
    머리글처럼 보이면(라벨이거나 연도/기수 표기) 머리글로 쓴다 — PDF엔
    THEAD가 없어 확신할 수 없으니 추측만 한다.
    """
    keep = parser._live_columns(grid, ncol)
    if not grid:
        return []

    # 물리 행이 하나뿐이면 그게 곧 유일한 데이터 행이다 — 머리글로 보고
    # 소비해버리면 rows가 비어 표 전체가 사라진다(실측: KB금융
    # "공정가치측정에 사용된 평가과정에 대한 기술" 표 — 산문 텍스트만
    # 있는 1행 6~8열 표가 _looks_like_header_cell()에 전부 라벨로
    # 판정되어 머리글로 소비되고 결과가 통째로 빈 리스트가 됐다).
    row0 = [grid[0][c] for c in keep if grid[0][c]]
    looks_header = (len(grid) > 1 and bool(row0)
                    and all(_looks_like_header_cell(c) for c in row0))
    start = 1 if looks_header else 0
    if looks_header:
        headers = [((grid[0][c].text if grid[0][c] else '') or '구분%d' % (i + 1))
                   for i, c in enumerate(keep)]
    else:
        headers = ['구분%d' % (i + 1) for i in range(len(keep))]

    rows = []
    for r in range(start, len(grid)):
        line = [parser._cell_text(grid[r][c]) if grid[r][c] else '' for c in keep]
        if any(x.strip() for x in line):
            rows.append(line)
    if not rows:
        return []
    return [('t', headers, rows)]


# 실측(사업연도/부터/까지, 업무상 연락처및 담당자/소속회사/부서 등)에서
# 진짜 "라벨:값" 관례는 물리 행 한 줄에 4칸을 넘기지 않았다. 5칸 이상인
# 표는 무조건 real-table로 낸다 — _table_looks_like_kv()가 값 칸이
# 서로 안 붙어있으면 못 잡아내기 때문이다. 실측 사례(KB금융 "일자/
# 제재기관/대상자/처벌내용/금전적제재금액/사유/근거법령" 7열 표):
# 값처럼 보이는 칸(날짜, "-")이 자유서술 텍스트 칸들 사이에 하나씩만
# 떨어져 있어 2칸 연속 값 런이 한 번도 안 생기고, kv 경로로 잘못
# 들어가 "-"를 빈 값으로 버리면서 사유/근거법령 등 여러 열이 통째로
# 사라졌다.
_MAX_KV_COLS = 4


def _table_block_to_chunks(parser, block):
    n_rows, n_cols = block.n_rows or 0, block.n_cols or 0
    if n_rows == 0 or n_cols == 0 or not block.cells:
        return []
    grid = _build_grid(block.cells, n_rows, n_cols)

    # 열≤2면 값이 한 칸뿐이라 항상 라벨:값 목록으로 안전하다. 3~4열은
    # 실제로 어느 행도 값이 옆칸과 이어붙지 않는지(_table_looks_like_kv)
    # 확인한 뒤에만 라벨:값 목록으로 본다. 5열 이상은 위 이유로 무조건
    # real-table.
    if n_cols <= 2 or (n_cols <= _MAX_KV_COLS
                        and _table_looks_like_kv(grid, n_cols)):
        return _kv_table_from_grid(parser, grid, n_cols)
    return _real_table_from_grid(parser, grid, n_cols)


# ══════════════════════════════════════════════════════════════════════
# 3. 표지 문단 → 키-값
# ══════════════════════════════════════════════════════════════════════

_RE_COVER_PERIOD = re.compile(
    r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*부터\s*사업연도\s*'
    r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*까지')

_RE_COVER_SUBMIT = re.compile(
    r'금융위원회\s*한국거래소\s*귀중\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일')

_RE_COVER_BODY = re.compile(
    r'제출대상법인\s*유형\s*[:：]\s*(?P<법인유형>.+?)\s*'
    r'면제사유발생\s*[:：]\s*(?P<면제사유>.+?)\s*'
    r'회\s*사\s*명\s*[:：]\s*(?P<회사명>.+?)\s*'
    r'대\s*표\s*이\s*사\s*[:：]\s*(?P<대표이사>.+?)\s*'
    r'본\s*점\s*소\s*재\s*지\s*[:：]\s*(?P<본점소재지>.+?)\s*'
    r'\(\s*전\s*화\s*\)\s*(?P<전화1>.+?)\s*'
    r'\(\s*홈페이지\s*\)\s*(?P<홈페이지>\S+)\s*'
    r'작\s*성\s*책\s*임\s*자\s*[:：]?\s*'
    r'\(\s*직\s*책\s*\)\s*(?P<직책>.+?)\s*'
    r'\(\s*성\s*명\s*\)\s*(?P<성명>.+?)\s*'
    r'\(\s*전\s*화\s*\)\s*(?P<전화2>.+)$')


def _iso_of(y, mo, d):
    return '%04d%02d%02d' % (int(y), int(mo), int(d))


def _extract_cover_kv(text):
    """표지 문단(예: '분 기 보 고 서 (제 25 기) 2024년 01월 01일 부터
    사업연도 2024년 03월 31일 까지 금융위원회 한국거래소 귀중 ...')을
    다른 periodic 문서와 같은 chunk 순서로 쪼갠다. 정규식이 매칭 안 되면
    빈 리스트를 돌려주고(호출부가 원래 문단을 그대로 남긴다) — 잘못
    쪼개는 것보다 안전하다.
    """
    m1 = _RE_COVER_PERIOD.search(text)
    m3 = _RE_COVER_BODY.search(text)
    if not (m1 and m3):
        return []

    y1, mo1, d1, y2, mo2, d2 = m1.groups()
    chunks = [
        ('kv', ['사업연도', '부터'], '%s년 %s월 %s일' % (y1, mo1, d1),
         None, _iso_of(y1, mo1, d1)),
        ('kv', ['사업연도', '까지'], '%s년 %s월 %s일' % (y2, mo2, d2),
         None, _iso_of(y2, mo2, d2)),
    ]

    m2 = _RE_COVER_SUBMIT.search(text)
    if m2:
        y, mo, d = m2.groups()
        chunks.append(('t', ['구분1', '구분2'],
                       [['금융위원회', ''],
                        ['한국거래소 귀중', '%s년 %s월 %s일' % (y, mo, d)]]))

    g = m3.groupdict()
    chunks += [
        ('kv', ['제출대상법인 유형 :'], g['법인유형'], None, None),
        ('kv', ['면제사유발생 :'], g['면제사유'], None, None),
        ('kv', ['회사명'], g['회사명'], None, None),
        ('kv', ['대표이사'], g['대표이사'], None, None),
        ('kv', ['본점소재지'], g['본점소재지'], None, None),
        ('t', ['구분1', '구분2'],
         [['', '(전 화) %s' % g['전화1']],
          ['', '(홈페이지) %s' % g['홈페이지']]]),
        ('kv', ['작성책임자'],
         '(직 책) %s (성 명) %s' % (g['직책'], g['성명']), None, None),
        ('p', '(전 화) %s' % g['전화2']),
    ]
    return chunks


# ══════════════════════════════════════════════════════════════════════
# 4. Block 목록 → chunk 목록
# ══════════════════════════════════════════════════════════════════════

def blocks_to_chunks(parser, blocks):
    chunks = []
    for b in blocks:
        if b.type == 'heading':
            level = min(max(b.level or 1, 1), 6)
            chunks.append(('h', level, b.text))
        elif b.type == 'paragraph':
            cover = _extract_cover_kv(b.text)
            chunks.extend(cover) if cover else chunks.append(('p', b.text))
        elif b.type == 'table':
            chunks.extend(_table_block_to_chunks(parser, b))
        # image 등은 지금은 건너뛴다 (pdf_parser.render_document()도 안 다룬다).
    return chunks


# ══════════════════════════════════════════════════════════════════════
# 5. periodic_parser 문서 dict 만들기
# ══════════════════════════════════════════════════════════════════════

def _guess_doc_type(blocks):
    for b in blocks:
        if b.type == 'heading' and b.text.strip().replace(' ', '').endswith('보고서'):
            return b.text.strip().replace(' ', '')
    return None


def pdf_to_periodic_doc(pdf_path, receipt_no=None, corp_name=None,
                        drop_empty=True, show_iso_date=True):
    result = pdf_parser.parse_pdf_document(pdf_path, source_file=str(pdf_path))
    parser = pp.PeriodicParser(drop_empty=drop_empty, show_iso_date=show_iso_date)
    chunks = blocks_to_chunks(parser, result.blocks)
    is_correction = any(
        c[0] == 'h' and '정정신고' in c[2].replace(' ', '') for c in chunks)
    return {
        '회사명': corp_name,
        '제출인_법인명': corp_name,
        '문서종류': _guess_doc_type(result.blocks),
        '서식버전': None,
        '접수번호': receipt_no,
        '정정공시': is_correction,
        'chunks': chunks,
    }


# ══════════════════════════════════════════════════════════════════════
# 6. 배치 실행 — raw/periodic 아래 *.pdf 3건을 찾아 결과를 저장한다
# ══════════════════════════════════════════════════════════════════════

def _find_dir(start, name):
    """start 아래를 훑어 이름이 name인 첫 디렉터리를 찾는다.

    한글 폴더명을 이 파일 안에 직접 타이핑하면 이 환경에서 유니코드
    정규화(NFC/NFD) 불일치로 실제 경로와 안 맞을 수 있어(윈도우 파일
    시스템 vs 이 소스 파일 인코딩), os.walk가 돌려주는 실제 경로 문자열만
    쓰고 한글 경로 조각을 직접 조립하지 않는다.
    """
    for dirpath, dirs, files in os.walk(start):
        if os.path.basename(dirpath) == name:
            return dirpath
    return None


def _raw_periodic_root():
    root = _find_dir(os.path.join(_REPO_ROOT, '1_data'), 'periodic')
    if root is None or 'raw' not in root.replace('\\', '/').split('/'):
        raise RuntimeError('corpus/raw/periodic 폴더를 못 찾았습니다: %r' % root)
    return root


def _iter_pdf_jobs(raw_root):
    for corp in sorted(os.listdir(raw_root)):
        corp_path = os.path.join(raw_root, corp)
        if not os.path.isdir(corp_path):
            continue
        for receipt_dir in sorted(os.listdir(corp_path)):
            receipt_path = os.path.join(corp_path, receipt_dir)
            if not os.path.isdir(receipt_path):
                continue
            for fn in os.listdir(receipt_path):
                if fn.lower().endswith('.pdf'):
                    yield corp, receipt_dir, os.path.join(receipt_path, fn)


def process_all(out_root=None, verbose=True):
    if out_root is None:
        out_root = os.path.join(_REPO_ROOT, 'results', 'parser', 'periodic')
    raw_root = _raw_periodic_root()

    written = []
    for corp, receipt_dir, pdf_path in _iter_pdf_jobs(raw_root):
        receipt_no = os.path.splitext(os.path.basename(pdf_path))[0]
        doc = pdf_to_periodic_doc(pdf_path, receipt_no=receipt_no, corp_name=corp)
        md = pp.to_markdown(doc, with_header=True)

        out_dir = os.path.join(out_root, corp, receipt_dir)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, receipt_no + '.md')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(md)
        written.append(out_path)

        # 예전 스크립트가 뷰어 HTML을 잘못 디코딩해 남긴 깨진 산출물을 치운다.
        for stale in (receipt_no + '_viewer.md', receipt_no + '_viewer.json'):
            stale_path = os.path.join(out_dir, stale)
            if os.path.exists(stale_path):
                os.remove(stale_path)

        if verbose:
            print('OK  %s  ->  %s' % (pdf_path, out_path))
    return written


if __name__ == '__main__':
    process_all()

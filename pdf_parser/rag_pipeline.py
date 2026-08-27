# -*- coding: utf-8 -*-
"""DART 공시 원문 → RAG 헤더가 붙은 마크다운 변환 파이프라인.

manifest.jsonl 의 메타데이터를 문서 최상단 헤더로 주입해서,
사용자가 한글·영문·법인격을 섞어 검색해도 임베딩 단계에서 걸리게 만든다.

    from rag_pipeline import process_conversion_pipeline
    process_conversion_pipeline(
        raw_root='../corpus/raw',
        out_dir='../corpus/rag',
        manifest_path='../corpus/manifest.jsonl',
    )

또는 명령줄에서

    python rag_pipeline.py ../corpus/raw ../corpus/rag ../corpus/manifest.jsonl

표준 라이브러리만 쓴다. 설치할 것이 없다.

────────────────────────────────────────────────────────────────────────
manifest.jsonl 실측 (4,204행)
────────────────────────────────────────────────────────────────────────
 · 필드 19개, 필수값 결측 0건, rcept_no·doc_id 중복 0건
 · doc_id = "{doc_group}_{rcept_no}"
 · corp_name == listed_name 인 행이 3,877건(92%) → 동의어 중복 제거 필수
 · corp_name 에 파일명 금지문자 없음. 단 'JYP Ent'(공백), '삼성E&A'(&) 존재
 · n_files 는 exchange/major/holding 이 1, periodic 만 최대 3
 · industry 8종: IT 건강관리 경기관련소비재 금융 산업재 소재
                커뮤니케이션서비스 필수소비재
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exchange_parser
import major_parser
import holding_parser
import periodic_parser

__all__ = [
    'ManifestIndex', 'CompanyAliasRegistry', 'build_aliases',
    'build_rag_header', 'convert_body', 'process_conversion_pipeline',
    'SUPPORTED_DOC_GROUPS',
]


# 파서를 갖춘 문서군. 넷 다 파서가 있다.
SUPPORTED_DOC_GROUPS = ('exchange', 'major', 'holding', 'periodic')

_RCEPT_RE = re.compile(r'^(\d{14})')
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_LEGAL_FORM = re.compile(
    r'\(\s*주\s*\)|주식회사|㈜|\(\s*유\s*\)|유한회사|\(\s*재\s*\)|재단법인'
    r'|\(\s*사\s*\)|사단법인|Co\.,?\s*Ltd\.?|Inc\.?|Corp\.?',
    re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════
# 1. manifest 색인
# ══════════════════════════════════════════════════════════════════════

class ManifestIndex:
    """manifest.jsonl 을 읽어 여러 열쇠로 찾을 수 있게 색인한다.

    manifest 는 프로그램 메모리에 상주하지 않는다. 실행할 때마다 읽는다.
    """

    def __init__(self, path):
        self.path = path
        self.rows = []
        self.by_rcept_no = {}
        self.by_doc_id = {}
        self.by_folder = {}       # 'raw/major/하이브/20260212001393' → row
        self._load()

    def _load(self):
        with open(self.path, encoding='utf-8') as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError as e:
                    raise ValueError('manifest %d행 파싱 실패: %s'
                                     % (lineno, e))
                # 문자열 필드의 앞뒤 공백을 턴다.
                # (DART list API 원본에는 report_nm 뒤에 공백이 붙어 있다)
                for k, v in list(row.items()):
                    if isinstance(v, str):
                        row[k] = v.strip()
                self.rows.append(row)
                self.by_rcept_no[row['rcept_no']] = row
                self.by_doc_id[row['doc_id']] = row
                fp = row.get('file_path')
                if fp:
                    self.by_folder[fp.replace('\\', '/').rstrip('/')] = row

    def __len__(self):
        return len(self.rows)

    # ── 찾기 ──────────────────────────────────────────────────────────
    def find(self, key):
        """접수번호 / doc_id / 파일경로 / 폴더경로 무엇으로도 찾는다."""
        if key is None:
            return None
        key = str(key)

        row = self.by_doc_id.get(key) or self.by_rcept_no.get(key)
        if row:
            return row

        norm = key.replace('\\', '/').rstrip('/')

        # 경로 꼬리를 잘라가며 manifest 의 file_path 와 맞춰본다
        parts = norm.split('/')
        for start in range(len(parts)):
            tail = '/'.join(parts[start:])
            row = self.by_folder.get(tail)
            if row:
                return row
            row = self.by_folder.get(os.path.dirname(tail))
            if row:
                return row

        # 파일명 앞 14자리 = 접수번호
        base = os.path.splitext(os.path.basename(norm))[0]
        m = _RCEPT_RE.match(base)
        if m:
            row = self.by_rcept_no.get(m.group(1))
            if row:
                return row

        # 상위 폴더 이름이 접수번호인 경우
        for p in reversed(parts):
            m = _RCEPT_RE.match(p)
            if m:
                row = self.by_rcept_no.get(m.group(1))
                if row:
                    return row
        return None


# ══════════════════════════════════════════════════════════════════════
# 2. 동의어 만들기
# ══════════════════════════════════════════════════════════════════════

def _strip_legal_form(name):
    """'(주)하이브' → '하이브'.  법인격 표기를 떼어낸다."""
    if not name:
        return ''
    return re.sub(r'\s{2,}', ' ', _LEGAL_FORM.sub('', name)).strip(' .,')


def build_aliases(meta, extra_aliases=()):
    """검색어 후보를 만든다. 순서를 지키면서 중복을 없앤다.

    corp_name == listed_name 인 행이 92%라 중복 제거가 반드시 필요하다.
    지침의 4개 형태에 더해, 공시 당시 옛 사명을 넣는다.
    '현대일렉트릭'으로 검색해도 'HD현대일렉트릭' 문서가 걸려야 하기 때문이다.
    """
    corp = (meta.get('corp_name') or '').strip()
    listed = (meta.get('listed_name') or '').strip()

    candidates = []
    if corp:
        bare = _strip_legal_form(corp)
        candidates += ['(주)%s' % bare, '%s주식회사' % bare]
    if listed:
        candidates.append(listed)
    if corp:
        candidates.append(corp)
        bare = _strip_legal_form(corp)
        if bare and bare != corp:
            candidates.append(bare)
    candidates += [a for a in extra_aliases if a]

    seen, out = set(), []
    for a in candidates:
        a = re.sub(r'\s{2,}', ' ', str(a)).strip()
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


# ══════════════════════════════════════════════════════════════════════
# 3. RAG 헤더
# ══════════════════════════════════════════════════════════════════════

def build_rag_header(meta, extra_aliases=(), include_sector=False):
    """마크다운 최상단에 붙일 헤더 문자열을 만든다.

        # [하이브] 주요사항보고서(자기주식 처분 결정)
        > 표준사명: 하이브 | 종목코드: 352820 | 업종: 커뮤니케이션서비스
        > 동의어/검색어: (주)하이브, 하이브주식회사, 하이브

    include_sector=True 면 업종 뒤에 섹터를 덧붙인다.
    """
    corp = (meta.get('corp_name') or '').strip() or '미상'
    report = (meta.get('report_nm') or '').strip() or '(보고서명 없음)'
    stock = (meta.get('stock_code') or '').strip() or '-'
    industry = (meta.get('industry') or '').strip() or '-'
    sector = (meta.get('sector') or '').strip()

    if include_sector and sector:
        industry = '%s > %s' % (industry, sector)

    aliases = build_aliases(meta, extra_aliases)

    lines = [
        '# [%s] %s' % (corp, report),
        '> 표준사명: %s | 종목코드: %s | 업종: %s' % (corp, stock, industry),
        '> 동의어/검색어: %s' % ', '.join(aliases),
    ]
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════
# 4. 본문 변환 (문서군별로 파서를 나눠 부른다)
# ══════════════════════════════════════════════════════════════════════

def convert_body(file_path, doc_group, corp_name=None, receipt_no=None,
                 drop_empty=True):
    """원문 파일 하나를 본문 마크다운으로 바꾼다.

    RAG 헤더가 최상단을 차지하므로 파서 자체 헤더는 끈다.
    반환: (본문 마크다운, 파서가 만든 문서 dict)
    """
    if doc_group == 'exchange':
        source = exchange_parser.decode(open(file_path, 'rb').read())
        if corp_name is None:
            corp_name = exchange_parser.corp_name_from_path(file_path)
        parser = exchange_parser.ExchangeParser(drop_empty=drop_empty)
        doc = parser.parse(source, receipt_no, corp_name)
        return exchange_parser.to_markdown(doc, with_header=False), doc

    if doc_group == 'major':
        source = major_parser.decode(open(file_path, 'rb').read())
        if corp_name is None:
            corp_name = major_parser.corp_name_from_path(file_path)
        parser = major_parser.MajorParser(drop_empty=drop_empty)
        doc = parser.parse(source, receipt_no, corp_name)
        return major_parser.to_markdown(doc, with_header=False), doc

    if doc_group == 'holding':
        source = holding_parser.decode(open(file_path, 'rb').read())
        if corp_name is None:
            corp_name = holding_parser.corp_name_from_path(file_path)
        parser = holding_parser.HoldingParser(drop_empty=drop_empty)
        doc = parser.parse(source, receipt_no, corp_name)
        return holding_parser.to_markdown(doc, with_header=False), doc

    if doc_group == 'periodic':
        source = periodic_parser.decode(open(file_path, 'rb').read())
        if corp_name is None:
            corp_name = periodic_parser.corp_name_from_path(file_path)
        parser = periodic_parser.PeriodicParser(drop_empty=drop_empty)
        doc = parser.parse(source, receipt_no, corp_name)
        return periodic_parser.to_markdown(doc, with_header=False), doc

    raise ValueError('파서가 없는 문서군입니다: %r (지원: %s)'
                     % (doc_group, ', '.join(SUPPORTED_DOC_GROUPS)))


def _old_name_of(doc, doc_group):
    """공시 당시 사명. 지금 이름과 다를 때만 돌려준다.

    CompanyAliasRegistry를 안 쓸 때(문서 하나만 보고 그 문서 자체의
    옛 이름만 넣던 예전 방식)를 위해 남겨뒀다. 기본 경로는 아래
    CompanyAliasRegistry — 문서군 4개를 통틀어 이름 표기를 모은다.
    """
    if not doc:
        return None
    if doc_group == 'exchange':
        return doc.get('공시당시_회사명') if doc.get('사명변경') else None
    if doc_group in ('major', 'holding', 'periodic'):
        # 셋 다 <COMPANY-NAME> 원문값을 '제출인_법인명'에 그대로 담아둔다.
        filed = doc.get('제출인_법인명')
        now = doc.get('회사명')
        if not filed or not now:
            return None
        # '(주)하이브' vs '하이브' 같은 법인격 차이는 옛 사명이 아니다.
        if _strip_legal_form(filed) == _strip_legal_form(now):
            return None
        return filed
    return None


# ══════════════════════════════════════════════════════════════════════
# 4b. 회사명 동의어 통합 — 문서군 네 개를 다 훑어 한 회사의 표기를 모은다
# ══════════════════════════════════════════════════════════════════════
#
# 문서군마다 회사명이 담기는 자리와 성격이 다르다.
#   exchange                HTML <title>       "공시 당시 표시명" (약칭 위주)
#   major/holding/periodic  XML <COMPANY-NAME>  "정식 등기 법인명" (전체 표기)
# 문서 한 건만 보고 동의어를 만들면 그 문서군에서 쓰는 표기만 들어간다.
# 예: HMM은 exchange의 <title>엔 항상 "HMM"만 있고, periodic/major/holding의
# <COMPANY-NAME>엔 "에이치엠엠 주식회사"/"에이치엠엠(주)" 등으로 적혀 있다.
# 그래서 "에이치엠엠"으로 검색하면 같은 회사인데 exchange 문서만 안 걸린다.
#
# CompanyAliasRegistry는 저장하기 전에 raw 전체를 한 번 가볍게(표·문단
# 처리 없이 회사명 태그 하나만) 훑어서 회사(corp_code)별로 표기를 전부
# 모아두고, 문서군에 상관없이 같은 회사면 같은 동의어 목록을 붙인다.

def _peek_name_variant(path, doc_group):
    """문서 하나에서 회사명 표기 하나만 가볍게 뽑는다.

    convert_body()처럼 표·문단까지 전부 처리하는 완전 파싱이 아니라,
    태그 트리만 만들어 회사명 태그 하나를 찾는다 — periodic의 큰
    재무제표 파일도 훨씬 가볍게 지나간다.
    """
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        if doc_group == 'exchange':
            source = exchange_parser.decode(raw)
            b = exchange_parser._TreeBuilder()
            b.feed(source)
            title_node = exchange_parser._find(b.root, 'title')
            if title_node is None:
                return None
            raw_title = exchange_parser.clean(
                exchange_parser._inner_html(title_node))
            parts = [p.strip() for p in raw_title.split('/')]
            return parts[0] if parts and parts[0] else None

        mod = {'major': major_parser, 'holding': holding_parser,
              'periodic': periodic_parser}.get(doc_group)
        if mod is None:
            return None
        source = mod.decode(raw)
        b = mod._TreeBuilder()
        b.feed(source)
        n = mod._find(b.root, 'COMPANY-NAME')
        return mod.flat(mod._text(n)) if n is not None else None
    except Exception:
        return None


class CompanyAliasRegistry:
    """회사(corp_code) 하나에 대해 문서군 네 개의 원문에서 나온 회사명
    표기를 전부 모아둔다. build() 로 raw 전체를 한 번 훑고,
    aliases_for(corp_code) 로 그 회사의 표기 목록(정렬됨)을 꺼낸다.
    """

    def __init__(self):
        self.by_corp = {}   # corp_code -> set(표기)

    def build(self, raw_root, index, doc_groups=SUPPORTED_DOC_GROUPS,
             verbose=True):
        wanted = set(doc_groups)
        files = []
        for dirpath, _, filenames in os.walk(raw_root):
            for fn in filenames:
                if fn.lower().endswith('.xml'):
                    files.append(os.path.join(dirpath, fn))

        n = 0
        for path in files:
            meta = index.find(path)
            if meta is None:
                continue
            doc_group = meta.get('doc_group')
            if doc_group not in wanted:
                continue
            corp_code = meta.get('corp_code')
            if not corp_code:
                continue
            variant = _peek_name_variant(path, doc_group)
            if variant:
                self.by_corp.setdefault(corp_code, set()).add(variant)
            n += 1
            if verbose and n % 1000 == 0:
                print('  동의어 수집 ... %d개' % n)
        if verbose:
            print('동의어 수집 완료: 문서 %d개 훑음, 회사 %d곳' % (n, len(self.by_corp)))

    def aliases_for(self, corp_code):
        return sorted(self.by_corp.get(corp_code) or ())


# ══════════════════════════════════════════════════════════════════════
# 5. 파일 이름
# ══════════════════════════════════════════════════════════════════════

def safe_filename(name):
    """윈도우에서 쓸 수 있는 이름으로 다듬는다."""
    name = _FORBIDDEN.sub('_', str(name))
    name = re.sub(r'\s{2,}', ' ', name).strip(' .')
    return name or '_'


def build_output_name(meta, file_path):
    """{corp_name}_{doc_id}.md

    한 폴더에 원문이 여러 개인 문서(periodic 최대 3개)는 파일 이름이 겹치므로
    접수번호 뒤에 붙은 꼬리(예: _00760)를 이름에 남긴다.
    """
    corp = safe_filename(meta.get('corp_name') or '미상')
    doc_id = safe_filename(meta.get('doc_id') or meta.get('rcept_no') or 'unknown')

    stem = os.path.splitext(os.path.basename(file_path))[0]
    m = _RCEPT_RE.match(stem)
    suffix = ''
    if m and stem != m.group(1):
        suffix = safe_filename(stem[len(m.group(1)):])
    return '%s_%s%s.md' % (corp, doc_id, suffix)


# ══════════════════════════════════════════════════════════════════════
# 6. 통합 파이프라인
# ══════════════════════════════════════════════════════════════════════

def process_one(file_path, index, out_dir, drop_empty=True,
                include_sector=False, add_old_name_alias=True,
                alias_registry=None, write=True):
    """파일 하나를 헤더+본문 마크다운으로 만들어 저장한다.

    alias_registry 를 주면 문서군 네 개를 통틀어 모은 회사명 표기를
    동의어로 쓴다(기본 경로). 안 주면 이 문서 자체의 옛 이름 하나만 쓴다
    (예전 방식, _old_name_of).

    반환: (상태, 메시지, 저장경로)
        상태 ∈ {'ok', 'no_manifest', 'unsupported', 'error'}
    """
    meta = index.find(file_path)
    if meta is None:
        return 'no_manifest', 'manifest에서 못 찾음', None

    doc_group = meta.get('doc_group')
    if doc_group not in SUPPORTED_DOC_GROUPS:
        return 'unsupported', '파서 없음: %s' % doc_group, None

    try:
        body, doc = convert_body(
            file_path, doc_group,
            corp_name=meta.get('corp_name'),
            receipt_no=meta.get('rcept_no'),
            drop_empty=drop_empty)
    except Exception as e:
        return 'error', repr(e), None

    extra = []
    if add_old_name_alias:
        if alias_registry is not None:
            extra = alias_registry.aliases_for(meta.get('corp_code'))
        else:
            old = _old_name_of(doc, doc_group)
            if old:
                extra.append(old)

    header = build_rag_header(meta, extra_aliases=extra,
                              include_sector=include_sector)
    text = header + '\n\n' + body.lstrip('\n')
    if not text.endswith('\n'):
        text += '\n'

    out_path = os.path.join(out_dir, build_output_name(meta, file_path))
    if write:
        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as w:
            w.write(text)
    return 'ok', text, out_path


def process_conversion_pipeline(raw_root, out_dir, manifest_path,
                                doc_groups=SUPPORTED_DOC_GROUPS,
                                drop_empty=True, include_sector=False,
                                add_old_name_alias=True,
                                unify_aliases_across_groups=True, limit=0,
                                verbose=True):
    """원문 폴더를 통째로 훑어 RAG 마크다운으로 바꾼다.

    raw_root      : corpus/raw (또는 그 하위 폴더)
    out_dir       : 결과를 담을 폴더. {corp_name}_{doc_id}.md 로 저장한다.
    manifest_path : manifest.jsonl 경로
    doc_groups    : **변환해서 저장할** 문서군. 기본은 넷 다.
    unify_aliases_across_groups : True(기본)면 동의어를 만들기 전에
        raw_root 전체(요청한 doc_groups 만이 아니라 네 문서군 전부)를
        훑어 회사별 이름 표기를 모은 뒤 문서군에 상관없이 같은 회사면
        같은 동의어 목록을 붙인다. raw_root 가 특정 문서군 하위 폴더
        하나뿐이면(예: corpus/raw/periodic) 다른 문서군 표기는 못 모으니
        효과가 없다 — corpus/raw 전체를 넘겨야 한다.
    limit         : 0 이면 전부, 양수면 그 개수만 (미리보기용)

    반환: 요약 dict
    """
    index = ManifestIndex(manifest_path)
    if verbose:
        print('manifest %d건 로드' % len(index))

    registry = None
    if add_old_name_alias and unify_aliases_across_groups:
        if verbose:
            print('회사명 동의어 사전 수집 중 (문서군 4개 전부 가볍게 훑음)...')
        registry = CompanyAliasRegistry()
        registry.build(raw_root, index, doc_groups=SUPPORTED_DOC_GROUPS,
                       verbose=verbose)

    files = []
    for dirpath, _, filenames in os.walk(raw_root):
        for fn in filenames:
            if fn.lower().endswith('.xml'):
                files.append(os.path.join(dirpath, fn))
    files.sort()

    wanted = set(doc_groups)
    counts = {'ok': 0, 'no_manifest': 0, 'unsupported': 0, 'error': 0,
              'skipped_group': 0}
    errors = []
    missing = []
    written = []

    for path in files:
        if limit and counts['ok'] + counts['error'] >= limit:
            break

        meta = index.find(path)
        if meta is None:
            counts['no_manifest'] += 1
            missing.append(path)
            continue
        if meta.get('doc_group') not in wanted:
            counts['skipped_group'] += 1
            continue

        status, msg, out_path = process_one(
            path, index, out_dir,
            drop_empty=drop_empty,
            include_sector=include_sector,
            add_old_name_alias=add_old_name_alias,
            alias_registry=registry)

        counts[status] = counts.get(status, 0) + 1
        if status == 'ok':
            written.append(out_path)
            if verbose and counts['ok'] % 200 == 0:
                print('  ... %d개 완료' % counts['ok'])
        elif status == 'error':
            errors.append((path, msg))

    if verbose:
        print('')
        print('변환 %d개 / 실패 %d개' % (counts['ok'], counts['error']))
        print('manifest 없음 %d개 / 파서 없는 문서군 %d개 / 대상 아님 %d개'
              % (counts['no_manifest'], counts['unsupported'],
                 counts['skipped_group']))
        print('결과 위치: %s' % os.path.abspath(out_dir))
        for p, m in errors[:10]:
            print('  실패: %s\n        %s' % (p, m))
        for p in missing[:10]:
            print('  manifest 없음: %s' % p)

    return {
        'counts': counts,
        'errors': errors,
        'missing_in_manifest': missing,
        'written': written,
        'manifest_rows': len(index),
    }


# ══════════════════════════════════════════════════════════════════════
# 7. 명령줄
# ══════════════════════════════════════════════════════════════════════

def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        description='DART 원문 → RAG 헤더 마크다운 변환 파이프라인')
    ap.add_argument('raw_root', help='원문 폴더 (예: ../corpus/raw)')
    ap.add_argument('out_dir', help='결과 폴더 (예: ../corpus/rag)')
    ap.add_argument('manifest', help='manifest.jsonl 경로')
    ap.add_argument('--groups', default=','.join(SUPPORTED_DOC_GROUPS),
                    help='변환할 문서군 (쉼표 구분)')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--keep-empty', action='store_true',
                    help="값이 '-' 인 항목도 남긴다")
    ap.add_argument('--with-sector', action='store_true',
                    help='업종 뒤에 섹터를 덧붙인다')
    ap.add_argument('--no-old-name', action='store_true',
                    help='옛 사명을 동의어에 넣지 않는다')
    ap.add_argument('--no-unify-aliases', action='store_true',
                    help='문서군 4개 통합 동의어 대신, 문서 하나의 옛 이름만 쓴다')
    a = ap.parse_args(argv)

    if not os.path.isdir(a.raw_root):
        print('원문 폴더가 없습니다: %s' % a.raw_root)
        return 1
    if not os.path.isfile(a.manifest):
        print('manifest 파일이 없습니다: %s' % a.manifest)
        return 1

    rep = process_conversion_pipeline(
        raw_root=a.raw_root,
        out_dir=a.out_dir,
        manifest_path=a.manifest,
        doc_groups=tuple(g.strip() for g in a.groups.split(',') if g.strip()),
        drop_empty=not a.keep_empty,
        include_sector=a.with_sector,
        add_old_name_alias=not a.no_old_name,
        unify_aliases_across_groups=not a.no_unify_aliases,
        limit=a.limit)
    return 0 if rep['counts']['error'] == 0 else 2


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))

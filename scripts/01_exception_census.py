# -*- coding: utf-8 -*-
"""1단계 — 예외 인벤토리 실측.

`docs/exception_matrix.md`의 근거 숫자를 만든다. 코퍼스를 읽기만 하고
아무것도 고치지 않는다(절대 규칙 1). 각 항목은 명세가 준 탐지법을 그대로
구현한다 — 명세의 숫자를 베끼지 않고 이 코퍼스에서 다시 잰다.

    E1 bare &        정규식 &(?!amp;|lt;|...)         → 문서별 건수
    E2 bare <        정규식 <(?![/?!]|[A-Za-z])       → 문서별 건수 + 표본
    E3 charset 오선언 한글 음절 비율                   → 5% 미만 문서
    E4 LIBRARY       //SECTION-2 수 vs 순회 도달 수    → 유실 수
    E5 표 아닌 TABLE  rows<=1 or cols<=1               → 비율
    E6 단위/각주 귀속 1x1 표의 (단위 …) 패턴 + 형제 위치 → 귀속 방향
    E7 회계기간 부재  '제 N 기' 표기 vs 날짜 동반 여부  → 미매핑 수
    E8 ACODE 반복    TE 총수 vs distinct ACODE 수      → dict 저장 시 유실률

사용법
    python scripts/01_exception_census.py --jobs 8
    python scripts/01_exception_census.py --sample 200   # 빠른 확인
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

sys.path.insert(0, P.PARSER_DIR)

# ── 탐지 정규식 (명세 그대로) ────────────────────────────────────────
RE_BARE_AMP = re.compile(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)')
RE_BARE_LT = re.compile(r'<(?![/?!]|[A-Za-z])')
RE_HANGUL = re.compile(r'[가-힣]')
RE_SECTION2 = re.compile(r'<\s*SECTION-2\b', re.IGNORECASE)
RE_UNIT_CAPTION = re.compile(r'\(\s*단\s*위\s*[:：]')
RE_FOOTNOTE_CAPTION = re.compile(r'^\s*[※*＊]')
# "제 55 기", "제55기 1분기말" — 기수 표기
RE_PERIOD = re.compile(r'제\s*\d{1,3}\s*기')
RE_DATE_ANY = re.compile(r'\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]?')

_G = {}


def _worker_init(manifest_path):
    import rag_pipeline
    import major_parser
    import holding_parser
    import periodic_parser
    import exchange_parser
    _G['index'] = rag_pipeline.ManifestIndex(manifest_path)
    _G['mod'] = {'major': major_parser, 'holding': holding_parser,
                 'periodic': periodic_parser, 'exchange': exchange_parser}


def _cells_of(mod, tr):
    return [c for c in tr.children if c.tag in mod.CELL_TAGS]


def _scan_tree(mod, root, rec):
    """E4/E5/E6/E8 — 트리를 한 번 훑으며 센다."""
    # E4: 순회 도달 SECTION-2 수. mod._walk 는 재귀 descendant 순회다.
    reached = 0
    tables = []
    for n in mod._walk(root):
        if n.tag == 'SECTION-2':
            reached += 1
        elif n.tag == 'TABLE':
            tables.append(n)
    rec['e4_section2_reached'] = reached

    # LIBRARY 컨테이너가 실제로 있는지, 그 아래 SECTION-2가 몇 개인지
    lib_sections = 0
    n_library = 0
    for n in mod._walk(root):
        if n.tag == 'LIBRARY':
            n_library += 1
            for d in mod._walk(n):
                if d.tag == 'SECTION-2':
                    lib_sections += 1
    rec['e4_library_nodes'] = n_library
    rec['e4_section2_under_library'] = lib_sections

    # E5/E6: 표 모양
    #
    # 귀속 방향은 "뒤쪽 어딘가에 표가 있나"로는 못 가린다 — 문서 중간의
    # 캡션은 앞뒤 양쪽에 거의 항상 표가 있다(1차 측정: 뒤 99.4% / 앞 83.3%).
    # 그래서 **바로 옆 형제**가 진짜 표인지를 본다. 그게 귀속이다.
    shape = {}          # id(node) -> (nrows, ncols) / None이면 표 아님
    n_tab = n_degenerate = 0
    for t in tables:
        trs = [e for e in mod._own_nodes(t) if e.tag == 'TR']
        rows = [r for r in (_cells_of(mod, tr) for tr in trs) if r]
        if not rows:
            continue
        n_tab += 1
        ncol = max(len(r) for r in rows)
        shape[id(t)] = (len(rows), ncol)
        if len(rows) <= 1 or ncol <= 1:
            n_degenerate += 1
    rec['e5_tables'] = n_tab
    rec['e5_degenerate'] = n_degenerate

    def _neighbours(node):
        """형제 순서에서 바로 앞/뒤의 TABLE 을 돌려준다."""
        sibs = [c for c in (node.parent.children if node.parent else [])
                if c.tag == 'TABLE' and id(c) in shape]
        try:
            i = sibs.index(node)
        except ValueError:
            return None, None
        return (sibs[i - 1] if i > 0 else None,
                sibs[i + 1] if i + 1 < len(sibs) else None)

    def _is_real(node):
        if node is None:
            return False
        r, c = shape[id(node)]
        return r > 1 and c > 1

    n_unit = n_foot = 0
    u_next = u_prev = u_both = u_neither = 0
    f_next = f_prev = f_both = f_neither = 0
    for t in tables:
        if id(t) not in shape:
            continue
        r, c = shape[id(t)]
        if r > 1 and c > 1:
            continue                      # 진짜 표는 캡션이 아니다
        txt = mod.flat(mod._text(t))
        is_unit = bool(RE_UNIT_CAPTION.search(txt))
        is_foot = (not is_unit) and bool(RE_FOOTNOTE_CAPTION.match(txt))
        if not (is_unit or is_foot):
            continue
        prev, nxt = _neighbours(t)
        pr, nx = _is_real(prev), _is_real(nxt)
        if is_unit:
            n_unit += 1
            if nx and pr:
                u_both += 1
            elif nx:
                u_next += 1
            elif pr:
                u_prev += 1
            else:
                u_neither += 1
        else:
            n_foot += 1
            if nx and pr:
                f_both += 1
            elif nx:
                f_next += 1
            elif pr:
                f_prev += 1
            else:
                f_neither += 1

    rec['e6_unit_captions'] = n_unit
    rec['e6_unit_next_only'] = u_next
    rec['e6_unit_prev_only'] = u_prev
    rec['e6_unit_both'] = u_both
    rec['e6_unit_neither'] = u_neither
    rec['e6_footnote_captions'] = n_foot
    rec['e6_foot_next_only'] = f_next
    rec['e6_foot_prev_only'] = f_prev
    rec['e6_foot_both'] = f_both
    rec['e6_foot_neither'] = f_neither

    # E8: 키 후보별 유실률.
    #
    # 명세는 (table_idx, row_idx, acode) 를 키로 쓰라고 했다. 그런데 1차
    # 측정에서 그 키도 23.9%를 잃었다 — 한 행 안에서 같은 ACODE 가 여러
    # 열에 반복되기 때문(당기/전기 같은 다열 구조). 그래서 열 번호까지
    # 넣은 키와, 아예 키를 쓰지 않는 리스트를 같이 잰다.
    te_total = 0
    acodes = set()
    triples = set()
    quads = set()
    same_row_repeat = 0
    for ti, t in enumerate(tables):
        trs = [e for e in mod._own_nodes(t) if e.tag == 'TR']
        for ri, tr in enumerate(trs):
            seen_in_row = set()
            for ci, c in enumerate(_cells_of(mod, tr)):
                if c.tag != 'TE':
                    continue
                code = c.attrs.get('ACODE')
                if not code:
                    continue
                te_total += 1
                acodes.add(code)
                triples.add((ti, ri, code))
                quads.add((ti, ri, ci, code))
                if code in seen_in_row:
                    same_row_repeat += 1
                seen_in_row.add(code)
    rec['e8_te_with_acode'] = te_total
    rec['e8_distinct_acode'] = len(acodes)
    rec['e8_distinct_triple'] = len(triples)
    rec['e8_distinct_quad'] = len(quads)
    rec['e8_same_row_repeat'] = same_row_repeat


def _scan(path):
    rec = {'source_path': os.path.relpath(path, P.REPO_ROOT).replace('\\', '/')}
    meta = _G['index'].find(path)
    if meta is None:
        rec.update(status='no_manifest')
        return rec
    rec['doc_id'] = meta.get('doc_id')
    rec['doc_group'] = meta.get('doc_group')
    mod = _G['mod'].get(rec['doc_group'])
    if mod is None:
        rec.update(status='unsupported')
        return rec

    try:
        with open(path, 'rb') as f:
            raw = f.read()
        rec['bytes'] = len(raw)
        # 절대 규칙 4 — 파서에 bytes를 넘기지 않는다. decode 먼저.
        source = mod.decode(raw)

        # E1 / E2 — 정제 전 원문 기준
        rec['e1_bare_amp'] = len(RE_BARE_AMP.findall(source))
        lts = RE_BARE_LT.findall(source)
        rec['e2_bare_lt'] = len(lts)
        rec['e2_samples'] = [m.group(0) for m in
                             re.finditer(r'<[^<>\n]{0,40}>', source)
                             if RE_BARE_LT.match(m.group(0))][:3]

        # E3 — 한글 음절 비율.
        #
        # 정규식으로 태그만 지운 문자열을 분모로 쓰면 안 된다. DSD-XML은
        # 속성이 길어서(USERMARK 등) 태그 제거가 새고, 남은 속성 문자열이
        # 분모를 부풀린다. 실측: periodic_20251114002900 은 한글 음절이
        # 198,955개나 있는 멀쩡한 13.7MB 문서인데 이 방식으로는 2.84%가
        # 나와 '한글 전량 파괴'로 잡힌다. E3의 존재 이유가 그 파괴를 잡는
        # 것인데 멀쩡한 문서에서 울리면 검사가 아니라 소음이다.
        # → 파서가 실제로 읽어낸 본문(트리 텍스트)을 분모로 쓴다.
        text_raw = re.sub(r'<[^>]{1,400}>', ' ', source)
        printable = len(re.sub(r'\s', '', text_raw))
        rec['e3_hangul_raw'] = len(RE_HANGUL.findall(text_raw))
        rec['e3_ratio_raw'] = (round(rec['e3_hangul_raw'] / printable, 4)
                               if printable else 0.0)

        # E4 — 정규식으로 센 SECTION-2 수 (순회 도달 수와 비교)
        rec['e4_section2_regex'] = len(RE_SECTION2.findall(source))

        if rec['doc_group'] == 'exchange':
            # 거래소공시는 HTML이라 SECTION/TE/ACODE 자체가 없다.
            # E4~E8은 '해당 없음'이며, 0으로 채우지 않고 빠뜨린다
            # (0건과 '측정 대상 아님'은 다른 사실이다).
            rec['tree_scan'] = 'n/a_html'
            body = text_raw
        else:
            b = mod._TreeBuilder()
            b.feed(source)
            body = mod._text(b.root)
            _scan_tree(mod, b.root, rec)
            rec['tree_scan'] = 'dsd_xml'

        # E3 본 측정 — 파서가 읽어낸 본문 기준
        chars = len(re.sub(r'\s', '', body))
        rec['e3_hangul'] = len(RE_HANGUL.findall(body))
        rec['e3_chars'] = chars
        rec['e3_ratio'] = round(rec['e3_hangul'] / chars, 4) if chars else 0.0

        # E7 — 기수 표기 vs 날짜 동반
        rec['e7_period_labels'] = len(RE_PERIOD.findall(body))
        rec['e7_dates'] = len(RE_DATE_ANY.findall(body))
        rec['status'] = 'ok'
    except Exception:
        rec.update(status='error', detail=traceback.format_exc(limit=8))
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description='1단계 예외 인벤토리 실측')
    ap.add_argument('--raw-root', default=P.RAW_ROOT)
    ap.add_argument('--manifest', default=P.MANIFEST_PATH)
    ap.add_argument('--sample', type=int, default=0, help='앞에서 N개만')
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument('--out', default=os.path.join(P.REPORTS_DIR,
                                                  'exception_census.jsonl'))
    a = ap.parse_args(argv)

    files = []
    for dirpath, _, filenames in os.walk(a.raw_root):
        for fn in filenames:
            if fn.lower().endswith('.xml'):
                files.append(os.path.join(dirpath, fn))
    files.sort()
    if a.sample:
        files = files[::max(1, len(files) // a.sample)][:a.sample]
    print('원문 %d개 스캔 (jobs=%d)' % (len(files), a.jobs))

    P.ensure_dirs(P.REPORTS_DIR)
    t0 = time.time()
    recs = []
    if a.jobs <= 1:
        _worker_init(a.manifest)
        for i, p in enumerate(files, 1):
            recs.append(_scan(p))
            if i % 200 == 0:
                print('  ... %d/%d (%.0fs)' % (i, len(files), time.time() - t0))
    else:
        with ProcessPoolExecutor(max_workers=a.jobs,
                                 initializer=_worker_init,
                                 initargs=(a.manifest,)) as ex:
            for i, r in enumerate(ex.map(_scan, files, chunksize=4), 1):
                recs.append(r)
                if i % 200 == 0:
                    print('  ... %d/%d (%.0fs)' % (i, len(files),
                                                   time.time() - t0))

    with open(a.out, 'w', encoding='utf-8') as w:
        for r in recs:
            w.write(json.dumps(r, ensure_ascii=False) + '\n')

    summarize(recs)
    print('')
    print('원자료: %s' % a.out)
    bad = [r for r in recs if r['status'] != 'ok']
    if bad:
        print('')
        print('스캔 실패 %d건:' % len(bad))
        for r in bad[:20]:
            print('  [%s] %s' % (r['status'], r.get('doc_id') or r['source_path']))
    return 0


def summarize(recs):
    ok = [r for r in recs if r['status'] == 'ok']
    if not ok:
        print('스캔 성공 0건')
        return
    groups = sorted(set(r['doc_group'] for r in ok))

    def tot(rs, k):
        return sum(r.get(k) or 0 for r in rs)

    def ndocs(rs, k, pred=lambda v: v > 0):
        return sum(1 for r in rs if pred(r.get(k) or 0))

    print('')
    print('═' * 78)
    print('예외 인벤토리 실측 — 문서 %d건' % len(ok))
    print('═' * 78)
    rows = []
    for g in groups + ['(전체)']:
        rs = ok if g == '(전체)' else [r for r in ok if r['doc_group'] == g]
        e4_lost = sum(max(0, (r.get('e4_section2_regex') or 0)
                          - (r.get('e4_section2_reached') or 0)) for r in rs)
        rows.append((
            g, len(rs),
            tot(rs, 'e1_bare_amp'), ndocs(rs, 'e1_bare_amp'),
            tot(rs, 'e2_bare_lt'), ndocs(rs, 'e2_bare_lt'),
            ndocs(rs, 'e3_ratio', lambda v: v < 0.05),
            tot(rs, 'e4_section2_regex'), tot(rs, 'e4_section2_reached'), e4_lost,
            tot(rs, 'e4_library_nodes'),
            tot(rs, 'e5_tables'), tot(rs, 'e5_degenerate'),
            tot(rs, 'e6_unit_captions'), tot(rs, 'e6_footnote_captions'),
            tot(rs, 'e7_period_labels'),
            tot(rs, 'e8_te_with_acode'), tot(rs, 'e8_distinct_acode'),
            tot(rs, 'e8_distinct_triple'), tot(rs, 'e8_distinct_quad'),
        ))

    hdr = ('문서군', '문서', 'E1건', 'E1문서', 'E2건', 'E2문서', 'E3<5%',
           'S2정규식', 'S2도달', 'S2유실', 'LIBRARY',
           'TABLE', '표아님', '단위캡션', '각주캡션', '기수표기',
           'TE', 'ACODE종', '(t,r,a)', '(t,r,c,a)')
    print(' | '.join('%-9s' % h for h in hdr))
    for r in rows:
        print(' | '.join('%-9s' % (('%s' % v) if isinstance(v, str) else
                                   '{:,}'.format(v)) for v in r))

    print('')
    print('E5 표 아닌 TABLE 비율: %.1f%%' % (
        100.0 * tot(ok, 'e5_degenerate') / max(1, tot(ok, 'e5_tables'))))
    te, ac = tot(ok, 'e8_te_with_acode'), tot(ok, 'e8_distinct_acode')
    print('E8 {acode: value} dict 저장 시 유실률: %.1f%%  (TE %s → ACODE종 %s)'
          % (100.0 * (te - ac) / max(1, te), '{:,}'.format(te),
             '{:,}'.format(ac)))
    tri = tot(ok, 'e8_distinct_triple')
    quad = tot(ok, 'e8_distinct_quad')
    print('   명세 키 (table_idx,row_idx,acode) 유실률: %.2f%%  (→ %s)'
          % (100.0 * (te - tri) / max(1, te), '{:,}'.format(tri)))
    print('   열 추가 (table_idx,row_idx,col_idx,acode) 유실률: %.2f%%  (→ %s)'
          % (100.0 * (te - quad) / max(1, te), '{:,}'.format(quad)))
    print('   같은 행에서 ACODE 반복: %s건'
          % '{:,}'.format(tot(ok, 'e8_same_row_repeat')))

    def pct(k, base):
        return 100.0 * tot(ok, k) / max(1, tot(ok, base))
    print('')
    print('E6 귀속 방향 — 바로 옆 형제가 진짜 표인가')
    print('  단위캡션 %s건: 뒤에만 %.1f%% / 앞에만 %.1f%% / 양쪽 %.1f%% / 없음 %.1f%%'
          % ('{:,}'.format(tot(ok, 'e6_unit_captions')),
             pct('e6_unit_next_only', 'e6_unit_captions'),
             pct('e6_unit_prev_only', 'e6_unit_captions'),
             pct('e6_unit_both', 'e6_unit_captions'),
             pct('e6_unit_neither', 'e6_unit_captions')))
    print('  각주캡션 %s건: 뒤에만 %.1f%% / 앞에만 %.1f%% / 양쪽 %.1f%% / 없음 %.1f%%'
          % ('{:,}'.format(tot(ok, 'e6_footnote_captions')),
             pct('e6_foot_next_only', 'e6_footnote_captions'),
             pct('e6_foot_prev_only', 'e6_footnote_captions'),
             pct('e6_foot_both', 'e6_footnote_captions'),
             pct('e6_foot_neither', 'e6_footnote_captions')))

    worst = sorted(ok, key=lambda r: r.get('e3_ratio') or 0)[:5]
    print('')
    print('E3 한글 비율 최저 5건:')
    for r in worst:
        print('  %.4f  %s  %s' % (r['e3_ratio'], r['doc_group'], r['doc_id']))

    lt = Counter()
    for r in ok:
        for s in (r.get('e2_samples') or []):
            lt[s] += 1
    if lt:
        print('')
        print('E2 bare < 표본 (상위 10):')
        for s, n in lt.most_common(10):
            print('  %4d  %s' % (n, s))


if __name__ == '__main__':
    sys.exit(main())

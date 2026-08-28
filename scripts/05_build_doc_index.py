# -*- coding: utf-8 -*-
"""5단계 — 문서 단위 메타 인덱스.

    data/index/documents.jsonl      문서 하나당 한 줄

왜 md 앞머리(front matter)가 아니라 별도 파일인가
────────────────────────────────────────────────────────────────────────
같은 사실을 두 군데 적으면 반드시 갈라진다. 식별 정보의 출처는
`corpus/manifest.jsonl`, 파생 정보의 출처는 `data/interim/docs/*.json.gz`
이고, 이 파일은 그 둘을 **옮겨 적는 것이 아니라 조인해서 얇게 펴 놓은 것**이다.
값을 여기서 새로 만들지 않는다 — 만들면 검증(`99_validate.py --index`)이 잡는다.

md 를 건드리지 않는 이유는 하나 더 있다. md 는 0단계 회귀 기준선이다.
헤더를 바꾸면 4,616건의 full 해시가 전부 바뀌는데, **지금 md 를 읽는 소비자가
없다.** LLM 에 가는 것은 조각(`data/processed/chunks.jsonl.gz`)이고, 조각은
이미 본문 첫 줄에 회사·보고서·접수일·섹션경로·단위·기수를 달고 있다.

이 파일이 답하는 질문
────────────────────────────────────────────────────────────────────────
    · 이 회사/업종/기간의 문서가 무엇이 있나          (필터)
    · 이 문서는 무엇을 담고 있나                      (toc / financials)
    · `제 55 기` 가 언제인가                          (periods, E7 결과)
    · 원문을 어디서 보나                              (dart_url / file_path)
    · 이 문서를 얼마나 믿어도 되나                    (parse_confidence,
                                                      periods_unresolved)

264MB 짜리 doc.json 을 열지 않고 4,204줄만 훑으면 된다.

    python scripts/05_build_doc_index.py
"""
import argparse
import collections
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

OUT = os.path.join(P.INDEX_DIR, 'documents.jsonl')

DART_URL = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=%s'

# manifest 에서 그대로 가져오는 값. file_path 는 원문 위치라 남기고,
# n_files 는 파생값(n_parts)으로 대체하므로 안 가져온다.
FROM_MANIFEST = ('corp_name', 'corp_code', 'stock_code', 'industry', 'sector',
                 'doc_group', 'doc_subtype', 'report_nm', 'is_correction',
                 'rcept_no', 'file_path')


def iso_dt(s):
    """'20230515' → '2023-05-15'. 이미 ISO 면 그대로."""
    s = (s or '').strip()
    if len(s) == 8 and s.isdigit():
        return '%s-%s-%s' % (s[:4], s[4:6], s[6:])
    return s or None


def fmt_period(rec):
    """기간 레코드 → 한 줄. 못 이은 것은 사전에 애초에 안 들어온다."""
    if rec.get('kind') == 'instant':
        return rec.get('date')
    if rec.get('kind') == 'duration':
        return '%s ~ %s' % (rec.get('start'), rec.get('end'))
    return None


def build_entry(meta, payload):
    """manifest 한 줄 + doc.json 하나 → 인덱스 한 줄.

    payload 가 None 이면 원문 XML 이 없는 문서다. 빼지 않고 status 로 남긴다 —
    조용히 사라진 문서와 애초에 없는 문서는 다른 사실이다 (절대 규칙 2).
    """
    e = {'doc_id': meta['doc_id'], 'status': 'ok' if payload else 'no_source_xml'}
    for k in FROM_MANIFEST:
        v = meta.get(k)
        if v not in (None, ''):
            e[k] = v
    e['rcept_dt'] = iso_dt(meta.get('rcept_dt'))
    for k in ('base_year', 'base_month'):
        if meta.get(k) not in (None, ''):
            e[k] = meta[k]
    # 제출인이 회사와 다를 때만 남긴다 (대량보유보고서는 제출인이 회사가 아니다)
    if meta.get('flr_nm') and meta['flr_nm'] != meta.get('corp_name'):
        e['flr_nm'] = meta['flr_nm']
    if meta.get('listed_name') and meta['listed_name'] != meta.get('corp_name'):
        e['listed_name'] = meta['listed_name']
    if meta.get('rcept_no'):
        e['dart_url'] = DART_URL % meta['rcept_no']

    if not payload:
        e['detail'] = 'XML 원문 없음 (file_format=%s)' % meta.get('file_format')
        return e

    toc = []
    n_tables = n_blocks = 0
    fin = set()
    conf = collections.Counter()
    periods = {}
    unresolved = 0
    corr_pairs = corr_changed = 0
    sources = []

    for part in payload.get('parts') or []:
        d = part.get('doc') or {}
        sources.append(part.get('source_path'))
        blocks = d.get('chunks') or []
        n_blocks += len(blocks)
        for c in blocks:
            # 목차는 h2 만. h3 이하는 문서당 29개까지 늘어나 인덱스가 뚱뚱해진다
            # (docs/chunking_notes.md 참조). h2 는 최대 23개다.
            if c[0] == 'h' and c[1] == 2 and c[2] not in toc:
                toc.append(c[2])
        n_tables += ((d.get('tables') or {}).get('n_tables') or 0)

        st = d.get('structured') or {}
        for g in st.get('financials') or []:
            fin.add(g['aclass'])
            conf[g.get('parse_confidence')] += 1
        cx = st.get('corrections') or {}
        corr_pairs += cx.get('n_pairs', 0)
        corr_changed += cx.get('n_changed', 0)

        pr = d.get('periods') or {}
        for lab, rec in (pr.get('map') or {}).items():
            v = fmt_period(rec)
            if v and lab not in periods:
                periods[lab] = v
        unresolved += pr.get('n_distinct', 0) - pr.get('n_resolved', 0)

    e['n_parts'] = len(payload.get('parts') or [])
    e['source_paths'] = [s for s in sources if s]
    e['n_blocks'] = n_blocks
    e['n_tables'] = n_tables
    e['toc'] = toc
    # 기수 사전은 그 문서가 **스스로 적은 것**만 들어 있다. 못 이은 것은
    # 개수로만 남긴다 — 채워 넣으면 문서가 말하지 않은 것을 말하게 된다.
    e['periods'] = periods
    e['periods_unresolved'] = unresolved
    if fin:
        e['financials'] = sorted(fin)
        e['parse_confidence'] = {k: v for k, v in sorted(conf.items()) if k}
    if corr_pairs:
        e['corrections'] = {'pairs': corr_pairs, 'changed': corr_changed}
    return e


def main(argv=None):
    ap = argparse.ArgumentParser(description='5단계 문서 메타 인덱스')
    ap.add_argument('--manifest', default=P.MANIFEST_PATH)
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args(argv)

    if not os.path.isdir(P.INTERIM_DOCS_DIR):
        print('doc.json 이 없다. scripts/03_build_docjson.py 를 먼저.')
        return 3

    metas = []
    with open(a.manifest, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                metas.append(json.loads(line))

    P.ensure_dirs(P.INDEX_DIR)
    rows = []
    missing = 0
    for m in sorted(metas, key=lambda r: r['doc_id']):
        path = os.path.join(P.INTERIM_DOCS_DIR, m['doc_id'] + '.json.gz')
        payload = None
        if os.path.isfile(path):
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                payload = json.load(f)
        else:
            missing += 1
        rows.append(build_entry(m, payload))

    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as w:
        for r in rows:
            w.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, a.out)

    n = lambda x: '{:,}'.format(x)
    by_group = collections.Counter(r.get('doc_group') for r in rows)
    with_toc = sum(1 for r in rows if r.get('toc'))
    with_per = sum(1 for r in rows if r.get('periods'))
    with_fin = sum(1 for r in rows if r.get('financials'))
    corr = sum(1 for r in rows if r.get('is_correction'))
    size = os.path.getsize(a.out)

    print('문서 %s줄 / %.1f MB' % (n(len(rows)), size / 1e6))
    print('  %-14s %s' % ('원문 없음', n(missing)))
    for g in ('exchange', 'major', 'holding', 'periodic'):
        print('  %-14s %s' % (g, n(by_group.get(g, 0))))
    print('')
    print('  목차(h2) 있음        %s' % n(with_toc))
    print('  기수→날짜 사전 있음  %s' % n(with_per))
    print('  {XBRL} 재무제표 있음 %s' % n(with_fin))
    print('  정정공시             %s' % n(corr))
    print('')
    print('산출: %s' % a.out)
    print('검증: python scripts/99_validate.py --index')
    return 0


if __name__ == '__main__':
    sys.exit(main())

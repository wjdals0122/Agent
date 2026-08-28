# -*- coding: utf-8 -*-
"""doc.json → chunks.jsonl.gz.

XML 을 다시 읽지 않는다. 축은 doc.json 이다(3단계). 그래서 청킹 전략을
바꿔도 5.5GB 를 재파싱하지 않고 이 스크립트만 다시 돌리면 된다.

    python scripts/06_build_chunks.py --jobs 10
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(P.REPO_ROOT, 'src'))

OUT = os.path.join(P.PROCESSED_DIR, 'chunks.jsonl.gz')

# 단위가 `unit` 필드에 없어도 행 라벨에 박혀 있으면(예: '계약금액(원)')
# 모델은 해석할 수 있다. 통계는 모델이 보는 본문 기준으로 센다.
# 단위 판정 정규식.
# ⚠ 맨 '원'·'주'·'건' 을 넣으면 안 된다. 한글에는  가 안 먹어서
#   '주요', '원활', '사건' 같은 낱말에 걸린다. 실제로 그렇게 만들었다가
#   "문서에 단위가 있다"가 항상 참이 되어 판정이 무의미해졌다.
#   괄호나 접두사가 붙어 단위임이 분명한 형태만 받는다.
RE_UNIT_ANY = re.compile(
    r'단\s*위\s*[:：]|\(단위|\(원\)|\(주\)|\(%\)|백만원|천원|억원|만원|조원'
    r'|원\)|주\)|％|%\)|미\s*달러|USD|천주|백만주')

STATS = os.path.join(P.REPORTS_DIR, 'chunk_stats.json')


def _one(fn):
    from chunk.build import build_chunks
    path = os.path.join(P.INTERIM_DOCS_DIR, fn)
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        p = json.load(f)
    meta = {'corp_name': p.get('corp_name'),
            'report_nm': p.get('report_nm'),
            'rcept_dt': p.get('rcept_dt')}
    out = []
    for part in p.get('parts') or []:
        out.extend(build_chunks(p, part, meta))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='doc.json → 청크')
    ap.add_argument('--jobs', type=int, default=min(61, max(1, (os.cpu_count() or 4) - 1)))  # 61: 윈도우 ProcessPoolExecutor 상한
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args(argv)

    if not os.path.isdir(P.INTERIM_DOCS_DIR):
        print('doc.json 이 없다. scripts/03_build_docjson.py 를 먼저.')
        return 3
    files = sorted(f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz'))
    if a.limit:
        files = files[:a.limit]
    print('doc.json %d개 → 청크 (jobs=%d)' % (len(files), a.jobs))

    P.ensure_dirs(P.PROCESSED_DIR, P.REPORTS_DIR)
    t0 = time.time()
    n = 0
    by_kind = {}
    unit_have = unit_miss = 0
    foot_chunks = foot_notes = 0        # E6 각주 귀속
    period_chunks = period_labels = 0   # E7 기수→날짜
    chars = 0
    conf = {}
    tmp = OUT + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as w:
        if a.jobs <= 1:
            it = (_one(f) for f in files)
        else:
            ex = ProcessPoolExecutor(max_workers=a.jobs)
            it = ex.map(_one, files, chunksize=4)
        for i, recs in enumerate(it, 1):
            for r in recs:
                w.write(json.dumps(r, ensure_ascii=False) + '\n')
                n += 1
                by_kind[r['kind']] = by_kind.get(r['kind'], 0) + 1
                chars += r['n_chars']
                if r['kind'] == 'table' and (r.get('numeric_ratio') or 0) >= 0.2:
                    if r.get('unit') or RE_UNIT_ANY.search(r.get('text') or ''):
                        unit_have += 1
                    else:
                        unit_miss += 1
                if r.get('footnotes'):
                    foot_chunks += 1
                    foot_notes += len(r['footnotes'])
                if r.get('period_dates'):
                    period_chunks += 1
                    period_labels += len(r['period_dates'])
                c = r.get('parse_confidence')
                if c:
                    conf[c] = conf.get(c, 0) + 1
            if i % 500 == 0:
                print('  ... %d/%d (%.0fs)' % (i, len(files), time.time() - t0))
        if a.jobs > 1:
            ex.shutdown()
    os.replace(tmp, OUT)

    numeric = unit_have + unit_miss
    stats = {
        'chunks': n, 'by_kind': by_kind, 'total_chars': chars,
        'avg_chars': round(chars / max(1, n)),
        'numeric_tables': numeric,
        'numeric_tables_with_unit': unit_have,
        'unit_coverage': round(unit_have / max(1, numeric), 4),
        'parse_confidence': conf,
        # E6 — 각주를 앞 표 조각에 붙인 결과. 안 붙인 각주는 문단 조각으로
        # 남는다(양옆에 진짜 표가 없는 45.2%).
        'footnote_chunks': foot_chunks,
        'footnotes_attached': foot_notes,
        # E7 — 기수에 날짜가 붙은 조각. 못 이은 기수는 라벨만 남는다.
        'period_dated_chunks': period_chunks,
        'period_dated_labels': period_labels,
        'elapsed_sec': round(time.time() - t0, 1),
    }
    with open(STATS, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print('')
    print('─' * 60)
    print('청크 %s개 / %.1fs' % ('{:,}'.format(n), time.time() - t0))
    for k in sorted(by_kind):
        print('  %-8s %s' % (k, '{:,}'.format(by_kind[k])))
    print('  평균 %s자' % '{:,}'.format(stats['avg_chars']))
    print('')
    print('숫자 표 청크 %s개 중 단위 보유 %s개 (%.1f%%)'
          % ('{:,}'.format(numeric), '{:,}'.format(unit_have),
             100 * stats['unit_coverage']))
    print('각주가 붙은 표 청크 %s개 (각주 %s건) — E6'
          % ('{:,}'.format(foot_chunks), '{:,}'.format(foot_notes)))
    print('기수에 날짜가 붙은 청크 %s개 (라벨 %s건) — E7'
          % ('{:,}'.format(period_chunks), '{:,}'.format(period_labels)))
    if conf:
        print('parse_confidence 물려받은 청크: %s' % conf)
    print('')
    print('산출: %s' % OUT)
    print('통계: %s' % STATS)
    return 0


if __name__ == '__main__':
    sys.exit(main())

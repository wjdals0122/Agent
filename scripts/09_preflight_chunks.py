# -*- coding: utf-8 -*-
"""임베딩 전 청크 계약 검사 — 4시간 돌리기 전에 30초로 잡는다.

    python scripts/09_preflight_chunks.py <청크폴더>
    python scripts/09_preflight_chunks.py <청크폴더> --sample 200000

무엇을 보는가
────────────────────────────────────────────────────────────────────────
    1. 파일 정렬 순서            sorted(glob) 이 곧 벡터 row 순서 계약이다
    2. 필수 6필드                embed_prepare 가 요구하는 것. 하나라도 없으면 중단
    3. chunk_id 고유성           중복 1건이라도 있으면 embed_prepare 가 중단
    4. chunk_id 구분자 ':'       chunk_store 가 콜론으로 문서를 가른다.
                                 '#' 이면 정정 재제출 필터가 조용히 죽는다
    5. doc_id 신규 여부          레포가 아는 문서인가, 새로 부여된 것인가
    6. 문서 커버리지             우리에게 있는데 청크에 없는 문서 = 결손

`data/index/documents.jsonl` 과 접수번호로 조인해서 대조한다(문서 단위 4,204건 —
XML 이 없는 pdf+html 3건도 여기에 있다). 그 파일이 없으면 5·6번은 건너뛴다.

manifest.json 의 `skipped_doc_ids` 는 커버리지에서 제외한다 — 대체된 옛 문서를
결손으로 세면 안 된다.

절대 규칙 2: 결손은 조용히 넘어가지 않는다. 못 붙인 것은 목록으로 출력한다.
"""
import argparse
import collections
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

REQUIRED = ('chunk_id', 'embedding_text', 'doc_id',
            'stock_code', 'disclosure_type', 'receipt_no')
DISPLAY = ('content', 'company', 'document_title', 'section_path')


def load_doc_index():
    path = os.path.join(P.INDEX_DIR, 'documents.jsonl')
    if not os.path.isfile(path):
        return None
    by_rcept = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            by_rcept.setdefault(r['rcept_no'], {
                'company': r['corp_name'],
                'disclosure_type': r['doc_group'],
                'report_nm': r['report_nm'],
                'status': r['status'],
            })
    return by_rcept


def load_skipped(chunks_dir):
    """청커가 '대체됐다'고 표시한 doc_id. 결손으로 세면 안 된다."""
    path = os.path.join(chunks_dir, 'manifest.json')
    if not os.path.isfile(path):
        return set()
    with open(path, encoding='utf-8') as f:
        return set(json.load(f).get('skipped_doc_ids') or ())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('chunks_dir')
    ap.add_argument('--glob', default='*.jsonl')
    ap.add_argument('--sample', type=int, default=0,
                    help='앞 N줄만 본다 (0=전량). 고유성·커버리지는 전량이어야 정확하다')
    a = ap.parse_args(argv)

    import glob as _glob
    files = sorted(_glob.glob(os.path.join(a.chunks_dir, a.glob)))
    if not files:
        print('청크 파일을 못 찾았다: %s' % os.path.join(a.chunks_dir, a.glob))
        return 3

    print('=' * 70)
    print('1. 파일 정렬 순서 — 이 순서가 벡터 row 순서다')
    print('=' * 70)
    for i, p in enumerate(files):
        print('  [%2d] %-52s %10.1f MB'
              % (i, os.path.basename(p), os.path.getsize(p) / 1024 / 1024))

    md = load_doc_index()
    skip_docs = load_skipped(a.chunks_dir)
    n = dup = 0
    missing_fields = collections.Counter()
    missing_display = collections.Counter()
    sep = collections.Counter()
    seen_ids = set()
    dup_samples = []
    bad_samples = collections.defaultdict(list)
    rcept_seen = set()
    n_skipped_rows = 0
    docid_by_rcept = collections.defaultdict(set)
    stop = False

    for path in files:
        with open(path, 'rb') as fh:
            for lineno, raw in enumerate(fh, 1):
                if stop:
                    break
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except Exception as e:
                    print('\n  JSON 파싱 실패 %s:%d — %s'
                          % (os.path.basename(path), lineno, e))
                    return 2
                n += 1

                for f in REQUIRED:
                    if f not in rec or rec[f] in (None, ''):
                        missing_fields[f] += 1
                        if len(bad_samples[f]) < 3:
                            bad_samples[f].append(
                                '%s:%d %s' % (os.path.basename(path), lineno,
                                              rec.get('chunk_id')))
                for f in DISPLAY:
                    if f not in rec or rec[f] in (None, ''):
                        missing_display[f] += 1

                cid = rec.get('chunk_id')
                if cid is not None:
                    if cid in seen_ids:
                        dup += 1
                        if len(dup_samples) < 5:
                            dup_samples.append(cid)
                    else:
                        seen_ids.add(cid)
                    if ':' in str(cid):
                        sep[':'] += 1
                    elif '#' in str(cid):
                        sep['#'] += 1
                    else:
                        sep['(없음)'] += 1

                if rec.get('doc_id') in skip_docs:
                    n_skipped_rows += 1
                    continue

                r = str(rec.get('receipt_no') or '')
                if r:
                    rcept_seen.add(r)
                    docid_by_rcept[r].add(rec.get('doc_id'))

                if a.sample and n >= a.sample:
                    stop = True
        if stop:
            break

    print()
    print('=' * 70)
    print('2~4. 계약 검사 — %s줄%s' % (f'{n:,}', ' (표본)' if a.sample else ''))
    print('=' * 70)

    ok = True
    if missing_fields:
        ok = False
        print('  [FAIL] 필수 필드 누락 — embed_prepare 가 여기서 중단한다')
        for f, c in missing_fields.most_common():
            print('     %-18s %8s건   예: %s' % (f, f'{c:,}', bad_samples[f][:2]))
    else:
        print('  [PASS] 필수 6필드 전부 채워짐')

    if dup:
        ok = False
        print('  [FAIL] chunk_id 중복 %s건 — embed_prepare 가 중단한다' % f'{dup:,}')
        print('     예: %s' % dup_samples)
    else:
        print('  [PASS] chunk_id 고유 (%s개)' % f'{len(seen_ids):,}')

    total_sep = sum(sep.values()) or 1
    if sep[':'] == total_sep:
        print("  [PASS] chunk_id 구분자 ':' — chunk_store 가 문서를 제대로 가른다")
    else:
        ok = False
        print("  [FAIL] chunk_id 구분자가 섞였다 — ':' 아니면 정정 재제출 필터가 조용히 죽는다")
        for k, c in sep.most_common():
            print('     %-8s %8s건  (%.1f%%)' % (k, f'{c:,}', c / total_sep * 100))

    for f, c in missing_display.most_common():
        print('  [주의] 표시용 %s 없음 %s건 — 검색 결과에 빈칸으로 나온다'
              % (f, f'{c:,}'))

    if md is None:
        print()
        print('  data/index/documents.jsonl 이 없어 5·6번을 건너뛴다.')
        print('  python scripts/05_build_doc_index.py')
        return 0 if ok else 2

    print()
    print('=' * 70)
    print('5. doc_id — 레포가 아는 문서인가')
    print('=' * 70)
    if skip_docs:
        print('  manifest 가 대체됐다고 표시: %d개 문서 / %s행 — 아래 집계에서 제외'
              % (len(skip_docs), f'{n_skipped_rows:,}'))
        for d in sorted(skip_docs):
            print('     %s' % d)
    known = rcept_seen & set(md)
    unknown = rcept_seen - set(md)
    print('  청크의 접수번호      %s' % f'{len(rcept_seen):,}')
    print('  레포가 아는 것       %s' % f'{len(known):,}')
    print('  레포가 모르는 것     %s' % f'{len(unknown):,}')
    if unknown:
        print('     예: %s' % sorted(unknown)[:8])
        print('     → manifest 에 없는 접수번호다. 새 문서이거나 접수번호가 변형됐다.')

    multi = {r: d for r, d in docid_by_rcept.items() if len(d) > 1}
    if multi:
        print('  한 접수번호에 doc_id 여러 개: %s건' % f'{len(multi):,}')
        for r in sorted(multi)[:5]:
            print('     %s → %s' % (r, sorted(multi[r])[:4]))
        print('     → 본문·첨부 분할이면 정상. 정정본을 새 doc 으로 뗀 것이면 의도 확인.')

    print()
    print('=' * 70)
    print('6. 문서 커버리지 — 조용히 빠진 것이 있는가')
    print('=' * 70)
    all_rcept = set(md)
    dropped = sorted(all_rcept - rcept_seen)
    print('  레포 문서            %s' % f'{len(all_rcept):,}')
    print('  청크에 있는 것       %s' % f'{len(all_rcept & rcept_seen):,}')
    print('  청크에 없는 것       %s' % f'{len(dropped):,}')
    if dropped:
        if a.sample:
            print('     (--sample 이라 이 숫자는 무의미하다. 전량으로 다시 볼 것)')
        else:
            ok = False
            print('     결손 목록:')
            for r in dropped[:40]:
                m = md[r]
                print('       %s  %-10s %-8s %s'
                      % (r, m['company'], m['disclosure_type'], m['report_nm']))
            if len(dropped) > 40:
                print('       ... 외 %d건' % (len(dropped) - 40))

    print()
    print('=' * 70)
    print('판정: %s' % ('통과 — 임베딩 진행 가능' if ok else '보류 — 위 FAIL 을 먼저 해결'))
    print('=' * 70)
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())

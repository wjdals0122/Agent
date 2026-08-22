# -*- coding: utf-8 -*-
"""검증 골든셋 — 6종을 돌리고 reports/에 CSV를 쓴다.

    --baseline   서술 md 해시가 0단계와 동일한가          (0단계 완료 조건)
    --docjson    doc.json 을 거쳐 렌더해도 동일한가        (3단계 완료 조건)
    --sanitize   이스케이프 횟수 × 4 = 문자수 증가분      (2단계 이후)
    --encoding   문서별 한글 음절 비율 >= 5%
    --structure  //SECTION-2 개수 = 순회 도달 개수
    --grid       표별 열 수 단일값 (ragged 0)
    --sums       {XBRL} 표의 총계 행 정합                 (5단계 이후)
    --all        전부

각 검사는 reports/validate_{name}.csv 를 남긴다. 요약은 진행률이 아니라
**실패 목록**을 출력한다. 종료 코드 0=전부 PASS, 2=FAIL 있음,
3=아직 못 도는 검사(선행 단계 미완).

사용법
    python scripts/99_validate.py --baseline
    python scripts/99_validate.py --baseline --jobs 8
    python scripts/99_validate.py --all
"""
import argparse
import csv
import importlib.util
import json
import os
import sys
import time

# 윈도우 콘솔은 cp949다. 요약에 '—'나 '⚠'가 섞였다고 검증이 죽으면 안 된다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

sys.path.insert(0, P.PARSER_DIR)


def _load_freezer():
    """00_freeze_baseline 은 숫자로 시작해 import 문으로 못 부른다."""
    path = os.path.join(P.SCRIPT_DIR, '00_freeze_baseline.py')
    spec = importlib.util.spec_from_file_location('freeze_baseline', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['freeze_baseline'] = mod      # 워커가 다시 import 할 수 있게
    spec.loader.exec_module(mod)
    return mod


def _write_csv(name, fieldnames, rows):
    P.ensure_dirs(P.REPORTS_DIR)
    path = os.path.join(P.REPORTS_DIR, 'validate_%s.csv' % name)
    with open(path, 'w', encoding='utf-8-sig', newline='') as w:
        wr = csv.DictWriter(w, fieldnames=fieldnames, extrasaction='ignore')
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    return path


# ══════════════════════════════════════════════════════════════════════
# 1. baseline — 서술 텍스트 바이트 동일
# ══════════════════════════════════════════════════════════════════════

def check_baseline(args):
    """지금 코드로 다시 변환해서 0단계 해시와 대조한다.

    비교 기준은 body(서술 텍스트)다. header는 manifest·동의어 registry에서
    나오므로 파서 리팩터링과 무관하고, 별도 열(header_same)로만 보고한다.
    """
    if not os.path.isfile(P.BASELINE_INDEX):
        print('베이스라인이 없다. 먼저 scripts/00_freeze_baseline.py 를 돌려라.')
        print('  없는 파일: %s' % P.BASELINE_INDEX)
        return 3, []

    frozen = {}
    with open(P.BASELINE_INDEX, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            frozen[r['source_path']] = r
    print('베이스라인 %d건 로드' % len(frozen))

    fz = _load_freezer()
    alias = fz.load_or_build_aliases(args.raw_root, args.manifest)
    files = fz.collect_sources(args.raw_root)
    if args.limit:
        files = files[:args.limit]

    # 재변환은 해시만 대조한다. md 를 다시 쓰지 않는다 —
    # 베이스라인 md 를 덮어쓸 위험도, 1.1GB 를 또 쌓을 이유도 없다.
    # (--write-md 로 실제 파일을 받아 diff 를 뜰 수 있다.)
    out_dir = os.path.join(P.DATA_DIR, 'verify', 'md')
    if args.write_md:
        P.ensure_dirs(out_dir)

    print('재변환 %d개 (jobs=%d)%s'
          % (len(files), args.jobs,
             (' → %s' % out_dir) if args.write_md else ' [md 안 씀]'))
    t0 = time.time()
    now = _run_converter(fz, files, args, alias, out_dir)
    print('  재변환 %.1fs' % (time.time() - t0))

    rows = []
    for rec in now:
        sp = rec['source_path']
        old = frozen.get(sp)
        if old is None:
            rows.append(dict(source_path=sp, doc_id=rec.get('doc_id'),
                             result='NEW', detail='베이스라인에 없던 원문',
                             body_same='', header_same=''))
            continue
        if rec['status'] != 'ok':
            rows.append(dict(source_path=sp, doc_id=rec.get('doc_id'),
                             result='FAIL', detail='변환 실패: %s' % rec['status'],
                             body_same='', header_same=''))
            continue
        body_same = rec['body_sha256'] == old['body_sha256']
        head_same = rec['full_sha256'] == old['full_sha256']
        rows.append(dict(
            source_path=sp, doc_id=rec.get('doc_id'),
            doc_group=rec.get('doc_group'),
            result='PASS' if body_same else 'FAIL',
            body_same=body_same, header_same=head_same,
            baseline_body=old['body_sha256'], current_body=rec['body_sha256'],
            baseline_bytes=old['bytes'], current_bytes=rec['bytes'],
            detail='' if body_same else '서술 텍스트 불일치 (%+d bytes)'
                   % (rec['bytes'] - old['bytes'])))

    if not args.limit:
        # --limit 미리보기에서는 '안 돌린 문서'를 결손으로 세면 안 된다.
        # 베이스라인에 있는데 전량 실행에서 안 나온 것만 진짜 MISSING이다.
        seen = set(r['source_path'] for r in rows)
        for sp, old in frozen.items():
            if sp not in seen:
                rows.append(dict(source_path=sp, doc_id=old.get('doc_id'),
                                 result='MISSING', detail='이번 실행에서 안 나옴',
                                 body_same='', header_same=''))
    else:
        print('  (--limit %d: 나머지 %d건은 이번에 안 돌렸다 — 결손으로 세지 않는다)'
              % (args.limit, len(frozen) - len(rows)))

    ok = sum(1 for r in rows if r['result'] == 'PASS')

    # 완료 조건은 '4,204건'이라는 문서 단위로 쓰여 있다. 파일 단위(4,616)와
    # 다르므로 둘 다 보고한다. XML 원문이 없어 애초에 변환 대상이 아닌
    # 문서는 parse_report 에 no_source_xml 로 남아 있고, 그것까지 합쳐야
    # 4,204 가 채워진다.
    covered = set(r['doc_id'] for r in rows
                  if r['result'] == 'PASS' and r.get('doc_id'))
    recorded = set()
    if os.path.isfile(P.PARSE_REPORT):
        with open(P.PARSE_REPORT, encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                if rec.get('status') == 'no_source_xml' and rec.get('doc_id'):
                    recorded.add(rec['doc_id'])
    total_docs = sum(1 for _ in open(P.MANIFEST_PATH, encoding='utf-8'))
    print('')
    print('doc_id 커버: %d PASS + %d no_source_xml(기록됨) = %d / %d'
          % (len(covered), len(recorded), len(covered | recorded), total_docs))
    if len(covered | recorded) != total_docs:
        missing = total_docs - len(covered | recorded)
        print('  ⚠ %d개 문서가 어느 쪽에도 안 잡혔다.' % missing)

    return (0 if ok == len(rows) else 2), rows


def _run_converter(fz, files, args, alias, out_dir):
    from concurrent.futures import ProcessPoolExecutor
    init_args = (args.manifest, alias, True, args.write_md)
    if args.jobs <= 1:
        fz.P.BASELINE_MD_DIR = out_dir
        fz._worker_init(*init_args)
        return [fz._convert(p) for p in files]
    out = []
    with ProcessPoolExecutor(max_workers=args.jobs,
                             initializer=_verify_init,
                             initargs=(init_args, out_dir)) as ex:
        for i, r in enumerate(ex.map(_verify_convert, files, chunksize=4), 1):
            out.append(r)
            if i % 500 == 0:
                print('  ... %d/%d' % (i, len(files)))
    return out


_VG = {}


def _verify_init(init_args, out_dir):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '00_freeze_baseline.py')
    spec = importlib.util.spec_from_file_location('freeze_baseline', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.P.BASELINE_MD_DIR = out_dir
    mod._worker_init(*init_args)
    _VG['fz'] = mod


def _verify_convert(path):
    return _VG['fz']._convert(path)


# ══════════════════════════════════════════════════════════════════════
# 1b. docjson — doc.json 을 거쳐 렌더한 결과가 0단계와 같은가
# ══════════════════════════════════════════════════════════════════════

def check_docjson(args):
    """3단계 완료 조건.

    renderer 의 입력이 XML 트리가 아니라 doc.json 이 됐다. XML 은 이제
    한 번만 파싱되고, 마크다운은 doc.json 에서만 나온다. 그 경로가
    0단계 해시와 바이트 동일한지 본다.
    """
    import gzip
    if not os.path.isfile(P.BASELINE_INDEX):
        print('베이스라인이 없다. scripts/00_freeze_baseline.py 를 먼저.')
        return 3, []
    if not os.path.isdir(P.INTERIM_DOCS_DIR):
        print('doc.json 이 없다. scripts/03_build_docjson.py 를 먼저.')
        return 3, []

    frozen = {}
    with open(P.BASELINE_INDEX, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            frozen[r['key']] = r
    print('베이스라인 %d건 로드' % len(frozen))

    sys.path.insert(0, os.path.join(P.REPO_ROOT, 'src'))
    from normalize import document

    files = sorted(f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz'))
    if args.limit:
        files = files[:args.limit]
    print('doc.json %d개에서 렌더 (직접 파싱 없음)' % len(files))

    rows = []
    seen_keys = set()
    t0 = time.time()
    for i, fn in enumerate(files, 1):
        path = os.path.join(P.INTERIM_DOCS_DIR, fn)
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as e:
            rows.append(dict(source_path=fn, doc_id=fn[:-8], result='FAIL',
                             detail='doc.json 읽기 실패: %r' % e,
                             body_same='', header_same=''))
            continue
        group = payload.get('doc_group')
        for part in payload.get('parts') or []:
            key = part['part_key']
            seen_keys.add(key)
            old = frozen.get(key)
            try:
                body = document.render(part['doc'], group, with_header=False)
            except Exception as e:
                rows.append(dict(source_path=part['source_path'],
                                 doc_id=payload['doc_id'], doc_group=group,
                                 result='FAIL', detail='렌더 실패: %r' % e,
                                 body_same='', header_same=''))
                continue
            if old is None:
                rows.append(dict(source_path=part['source_path'],
                                 doc_id=payload['doc_id'], doc_group=group,
                                 result='NEW', detail='베이스라인에 없는 키 %s' % key,
                                 body_same='', header_same=''))
                continue
            got = P.sha256_text(body)
            same = got == old['body_sha256']
            rows.append(dict(
                source_path=part['source_path'], doc_id=payload['doc_id'],
                doc_group=group, result='PASS' if same else 'FAIL',
                body_same=same, header_same='',
                baseline_body=old['body_sha256'], current_body=got,
                detail='' if same else 'doc.json 경유 렌더가 베이스라인과 다름'))
        if i % 500 == 0:
            print('  ... %d/%d (%.0fs)' % (i, len(files), time.time() - t0))

    if not args.limit:
        for key, old in frozen.items():
            if key not in seen_keys:
                rows.append(dict(source_path=old['source_path'],
                                 doc_id=old.get('doc_id'), result='MISSING',
                                 detail='doc.json 에 이 part 가 없다',
                                 body_same='', header_same=''))

    n_docs = len(files)
    print('')
    print('doc.json %d개 / part %d개 / 렌더 %.1fs'
          % (n_docs, len(seen_keys), time.time() - t0))
    ok = sum(1 for r in rows if r['result'] == 'PASS')
    return (0 if ok == len(rows) else 2), rows


# ══════════════════════════════════════════════════════════════════════
# 2~6. 아직 선행 단계가 없는 검사
# ══════════════════════════════════════════════════════════════════════

def _not_ready(name, needs):
    def run(args):
        print('%s: 선행 단계 미완 — %s' % (name, needs))
        return 3, []
    return run


CHECKS = {
    'baseline': check_baseline,
    'docjson': check_docjson,
    'sanitize': _not_ready('sanitize', '2단계 normalize/sanitize.py'),
    'encoding': _not_ready('encoding', '2단계 normalize/encoding.py'),
    'structure': _not_ready('structure', '2단계 normalize/tree.py'),
    'grid': _not_ready('grid', '2단계 normalize/grid.py'),
    'sums': _not_ready('sums', '5단계 extract/financials.py'),
}

FIELDS = ['result', 'doc_id', 'doc_group', 'source_path', 'body_same',
          'header_same', 'baseline_body', 'current_body', 'baseline_bytes',
          'current_bytes', 'detail']


def main(argv=None):
    ap = argparse.ArgumentParser(description='검증 골든셋')
    for name in CHECKS:
        ap.add_argument('--' + name, action='store_true')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--raw-root', default=P.RAW_ROOT)
    ap.add_argument('--manifest', default=P.MANIFEST_PATH)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument('--write-md', action='store_true',
                    help='재변환 md 를 data/verify/md 에 실제로 쓴다 '
                         '(불일치가 났을 때 diff 뜨려고). 기본은 해시만.')
    a = ap.parse_args(argv)

    wanted = [n for n in CHECKS if getattr(a, n)] or (list(CHECKS) if a.all else [])
    if not wanted:
        ap.error('검사를 하나 이상 지정해라 (--baseline / --all 등)')

    worst = 0
    summary = []
    for name in wanted:
        print('')
        print('═' * 70)
        print('검사: %s' % name)
        print('═' * 70)
        code, rows = CHECKS[name](a)
        worst = max(worst, code)
        if rows:
            path = _write_csv(name, FIELDS, rows)
            counts = {}
            for r in rows:
                counts[r['result']] = counts.get(r['result'], 0) + 1
            total = len(rows)
            npass = counts.get('PASS', 0)
            print('')
            print('%d / %d PASS' % (npass, total))
            for k in sorted(counts):
                if k != 'PASS':
                    print('  %-8s %d' % (k, counts[k]))
            bad = [r for r in rows if r['result'] != 'PASS']
            if bad:
                print('')
                print('실패 목록 (앞 40건):')
                for r in bad[:40]:
                    print('  [%s] %s  %s' % (r['result'],
                                             r.get('doc_id') or r['source_path'],
                                             r.get('detail') or ''))
                if len(bad) > 40:
                    print('  ... 외 %d건 (전체는 CSV 참조)' % (len(bad) - 40))
            print('CSV: %s' % path)
            summary.append((name, npass, total))
        else:
            summary.append((name, 0, 0))

    print('')
    print('═' * 70)
    for name, npass, total in summary:
        if total == 0:
            print('  %-10s  —  (못 돌았음)' % name)
        else:
            print('  %-10s  %s  %d/%d'
                  % (name, 'PASS' if npass == total else 'FAIL', npass, total))
    return worst


if __name__ == '__main__':
    sys.exit(main())

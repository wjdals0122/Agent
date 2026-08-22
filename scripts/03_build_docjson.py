# -*- coding: utf-8 -*-
"""3단계 — XML → doc.json.

    data/interim/docs/{doc_id}.json.gz     문서 하나당 파일 하나
    data/interim/parse_report.jsonl        전 문서 처리 기록

문서 하나당 파일 하나로 쪼개는 이유는 **실패 격리와 --only-failed
재실행**이다. 4,204개 중 3개가 깨졌을 때 나머지 4,201개를 다시 파싱하지
않는다. periodic 은 한 접수번호에 원문이 최대 3개(본보고서 +
감사보고서 00760 + 연결감사보고서 00761)라, 그것들은 같은 doc.json 안의
`parts` 로 들어간다 — 접수번호 하나가 문서 하나다.

멱등: 원문 mtime + config 해시로 스킵 판정.

    python scripts/03_build_docjson.py --jobs 10
    python scripts/03_build_docjson.py --only-failed
    python scripts/03_build_docjson.py --force
"""
import argparse
import gzip
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(P.REPO_ROOT, 'src'))
sys.path.insert(0, P.PARSER_DIR)

CONFIG = {'drop_empty': True, 'schema': 'dart.doc/1', 'ver': 1}
CONFIG_HASH = P.config_hash(CONFIG)

_G = {}


def _init(manifest_path):
    import rag_pipeline
    from normalize import document
    _G['index'] = rag_pipeline.ManifestIndex(manifest_path)
    _G['document'] = document


def out_path(doc_id):
    return os.path.join(P.INTERIM_DOCS_DIR, doc_id + '.json.gz')


def _needs_build(doc_id, files):
    """멱등 판정 — 원문 mtime 과 config 해시가 그대로면 건너뛴다."""
    p = out_path(doc_id)
    if not os.path.isfile(p):
        return True
    try:
        with gzip.open(p, 'rt', encoding='utf-8') as f:
            old = json.load(f)
    except Exception:
        return True            # 깨진 파일은 다시 만든다
    if old.get('_config_hash') != CONFIG_HASH:
        return True
    stamps = old.get('_source_mtimes') or {}
    for fp in files:
        rel = os.path.relpath(fp, P.REPO_ROOT).replace('\\', '/')
        if abs(stamps.get(rel, -1) - os.path.getmtime(fp)) > 1e-6:
            return True
    return False


def build_one(job):
    """job = (doc_id, [원문경로…]). 예외는 밖으로 안 새고 기록된다."""
    doc_id, files = job
    index, document = _G['index'], _G['document']
    t0 = time.time()
    rec = {'doc_id': doc_id, 'stage': 'docjson',
           'n_source_files': len(files)}

    meta = None
    parts = []
    part_recs = []
    for fp in sorted(files):
        rel = os.path.relpath(fp, P.REPO_ROOT).replace('\\', '/')
        m = index.find(fp)
        if m is None:
            part_recs.append({'source_path': rel, 'status': 'no_manifest',
                              'detail': 'manifest에서 못 찾음'})
            continue
        meta = meta or m
        try:
            with open(fp, 'rb') as f:
                raw = f.read()
            doc, actions = document.build_doc(
                raw, m['doc_group'], file_path=fp,
                corp_name=m.get('corp_name'), receipt_no=m.get('rcept_no'),
                drop_empty=CONFIG['drop_empty'])
            parts.append({
                'part_key': document.part_key_for(doc_id, fp),
                'source_path': rel,
                'source_bytes': len(raw),
                'doc': doc,
                'actions': actions,
            })
            part_recs.append({'source_path': rel, 'status': 'ok',
                              'n_chunks': len(doc.get('chunks') or []),
                              'n_actions': len(actions)})
        except Exception:
            part_recs.append({'source_path': rel, 'status': 'error',
                              'detail': traceback.format_exc(limit=12)})

    rec['parts'] = part_recs
    rec['status'] = ('ok' if parts and all(p['status'] == 'ok'
                                           for p in part_recs)
                     else ('partial' if parts else 'error'))
    if not parts:
        rec['elapsed'] = round(time.time() - t0, 3)
        return rec

    payload = document.doc_to_json(doc_id, meta or {}, parts)
    payload['_config_hash'] = CONFIG_HASH
    payload['_source_mtimes'] = {
        os.path.relpath(fp, P.REPO_ROOT).replace('\\', '/'):
            os.path.getmtime(fp) for fp in files}

    P.ensure_dirs(P.INTERIM_DOCS_DIR)
    tmp = out_path(doc_id) + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as w:
        json.dump(payload, w, ensure_ascii=False)
    os.replace(tmp, out_path(doc_id))     # 반쯤 쓰인 파일을 남기지 않는다

    rec['out'] = os.path.basename(out_path(doc_id))
    rec['bytes'] = os.path.getsize(out_path(doc_id))
    rec['elapsed'] = round(time.time() - t0, 3)
    return rec


def collect_jobs(raw_root, manifest_path):
    """doc_id → 원문 파일 목록. manifest 의 file_path 폴더를 기준으로 묶는다."""
    jobs = {}
    no_xml = []
    with open(manifest_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            folder = os.path.join(P.REPO_ROOT, 'corpus',
                                  row['file_path'].replace('/', os.sep))
            xmls = []
            if os.path.isdir(folder):
                xmls = sorted(os.path.join(folder, fn)
                              for fn in os.listdir(folder)
                              if fn.lower().endswith('.xml'))
            if xmls:
                jobs[row['doc_id']] = xmls
            else:
                no_xml.append(row)
    return jobs, no_xml


def main(argv=None):
    ap = argparse.ArgumentParser(description='3단계 doc.json 생성')
    ap.add_argument('--raw-root', default=P.RAW_ROOT)
    ap.add_argument('--manifest', default=P.MANIFEST_PATH)
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true', help='멱등 스킵 무시')
    ap.add_argument('--only-failed', action='store_true',
                    help='지난 실행에서 ok 가 아니었던 문서만 다시')
    a = ap.parse_args(argv)

    P.ensure_dirs(P.INTERIM_DOCS_DIR)
    jobs, no_xml = collect_jobs(a.raw_root, a.manifest)
    print('manifest 문서 %d개 / XML 있는 문서 %d개 / XML 없는 문서 %d개'
          % (len(jobs) + len(no_xml), len(jobs), len(no_xml)))

    if a.only_failed:
        prev = {}
        if os.path.isfile(P.PARSE_REPORT):
            with open(P.PARSE_REPORT, encoding='utf-8') as f:
                for line in f:
                    r = json.loads(line)
                    if r.get('doc_id'):
                        prev[r['doc_id']] = r.get('status')
        jobs = {k: v for k, v in jobs.items() if prev.get(k) != 'ok'}
        print('--only-failed: %d개만 다시' % len(jobs))

    todo = list(jobs.items())
    if not a.force:
        skipped = [k for k, v in todo if not _needs_build(k, v)]
        todo = [(k, v) for k, v in todo if k not in set(skipped)]
        if skipped:
            print('멱등 스킵 %d개 (원문 mtime + config 해시 동일)' % len(skipped))
    if a.limit:
        todo = todo[:a.limit]
    todo.sort()

    print('생성 대상 %d개 (jobs=%d)' % (len(todo), a.jobs))
    t0 = time.time()
    recs = []
    if todo:
        if a.jobs <= 1:
            _init(a.manifest)
            for i, j in enumerate(todo, 1):
                recs.append(build_one(j))
                if i % 200 == 0:
                    print('  ... %d/%d (%.0fs)' % (i, len(todo), time.time() - t0))
        else:
            with ProcessPoolExecutor(max_workers=a.jobs, initializer=_init,
                                     initargs=(a.manifest,)) as ex:
                for i, r in enumerate(ex.map(build_one, todo, chunksize=2), 1):
                    recs.append(r)
                    if i % 200 == 0:
                        print('  ... %d/%d (%.0fs)'
                              % (i, len(todo), time.time() - t0))

    for row in no_xml:
        recs.append({'doc_id': row['doc_id'], 'doc_group': row.get('doc_group'),
                     'stage': 'collect', 'status': 'no_source_xml',
                     'detail': 'XML 원문 없음 (file_format=%s) — 변환 대상 아님'
                               % row.get('file_format')})

    with open(P.PARSE_REPORT, 'w', encoding='utf-8') as w:
        for r in sorted(recs, key=lambda r: r['doc_id']):
            w.write(json.dumps(r, ensure_ascii=False) + '\n')

    on_disk = len([f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz')])
    by = {}
    for r in recs:
        by[r['status']] = by.get(r['status'], 0) + 1
    total_bytes = sum(r.get('bytes') or 0 for r in recs)

    print('')
    print('─' * 66)
    print('경과 %.1fs' % (time.time() - t0))
    for k in sorted(by):
        print('  %-16s %d' % (k, by[k]))
    print('  %-16s %d' % ('doc.json 파일', on_disk))
    if total_bytes:
        print('  %-16s %.1f MB (이번에 쓴 것)' % ('크기', total_bytes / 1e6))

    bad = [r for r in recs if r['status'] not in ('ok', 'no_source_xml')]
    if bad:
        print('')
        print('실패 목록 (%d건):' % len(bad))
        for r in bad[:40]:
            print('  [%s] %s' % (r['status'], r['doc_id']))
            for p in (r.get('parts') or []):
                if p['status'] != 'ok':
                    last = (p.get('detail') or '').strip().splitlines()
                    print('        %s  %s' % (p['source_path'],
                                              last[-1] if last else p['status']))
        print('  → python scripts/03_build_docjson.py --only-failed')
    print('')
    print('산출: %s' % P.INTERIM_DOCS_DIR)
    print('기록: %s' % P.PARSE_REPORT)
    return 0 if not bad else 2


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""0단계 — 회귀 베이스라인 동결.

기존 변환기(`parser/rag_pipeline.py`)를 **고치지 않고** 라이브러리로 불러
corpus/raw 전량을 md로 만들고, 파일별 sha256을 `data/baseline/`에 얼린다.
이후 모든 리팩터링은 여기 대비 **서술 텍스트 바이트 동일**을 지켜야 한다.

산출물
    baseline/hash/{doc_id}[{suffix}].md.sha256      파일 하나당 해시 하나 (git 추적)
    baseline/index.jsonl                            위 해시의 통합 색인 (git 추적)
    data/baseline_md/{corp}_{doc_id}[{suffix}].md   그 해시를 만든 md 원본 (1.1GB, 미추적)
    data/interim/parse_report.jsonl                 전 문서 처리 기록
    data/interim/alias_registry.json                동의어 prescan 캐시

왜 해시를 두 개 남기나
    text = header + 빈 줄 + body.
    header는 manifest·동의어 registry에서 나오고 body는 파서에서 나온다.
    리팩터링이 지켜야 하는 것은 body다. 그래서 full/body 둘 다 기록하고
    검증은 body를 기준으로 한다.

절대 규칙 대응
    · raw는 읽기 전용 — 'rb' 읽기 외에 원문 경로를 건드리지 않는다.
    · 예외를 삼키지 않는다 — ok/error/no_manifest/unsupported/no_source_xml
      전부 parse_report.jsonl에 한 줄씩 남는다. 성공도 남는다.
    · 진행률이 아니라 실패 목록을 출력한다.

사용법
    python scripts/00_freeze_baseline.py            # 전량 동결
    python scripts/00_freeze_baseline.py --limit 50 # 미리보기
    python scripts/00_freeze_baseline.py --jobs 8   # 병렬
    python scripts/00_freeze_baseline.py --force    # 캐시 무시하고 재동결
"""
import argparse
import json
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

# 윈도우 콘솔은 cp949다. 진행 로그에 '—' 하나 섞였다고 실행이 죽으면 안 된다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

sys.path.insert(0, P.PARSER_DIR)


# ══════════════════════════════════════════════════════════════════════
# 워커 (Windows spawn — 모듈 전역에 물려둔다)
# ══════════════════════════════════════════════════════════════════════

_G = {}


def _worker_init(manifest_path, alias_by_corp, drop_empty, write=True):
    import rag_pipeline
    _G['rp'] = rag_pipeline
    _G['index'] = rag_pipeline.ManifestIndex(manifest_path)
    reg = rag_pipeline.CompanyAliasRegistry()
    reg.by_corp = {k: set(v) for k, v in alias_by_corp.items()}
    _G['registry'] = reg
    _G['drop_empty'] = drop_empty
    # 검증 재변환은 해시만 필요하다. md 1.1GB를 다시 쓸 이유가 없다.
    _G['write'] = write


def _convert(path):
    """원문 하나 → 처리 기록 dict. 예외는 절대 밖으로 새지 않고 기록된다."""
    rp, index, registry = _G['rp'], _G['index'], _G['registry']
    # 경로는 NFC 로 통일한다 — 기계마다 다른 정규화로 적히면 베이스라인
    # 대조가 통째로 'NEW' 가 된다 (P.rel 주석 참조).
    rec = {'source_path': P.rel(path)}
    meta = index.find(path)
    if meta is None:
        rec.update(status='no_manifest', stage='index',
                   detail='manifest에서 못 찾음')
        return rec

    rec['doc_id'] = meta.get('doc_id')
    rec['doc_group'] = meta.get('doc_group')
    rec['corp_code'] = meta.get('corp_code')
    rec['corp_name'] = meta.get('corp_name')

    try:
        status, msg, out_path = rp.process_one(
            path, index, P.BASELINE_MD_DIR,
            drop_empty=_G['drop_empty'],
            include_sector=False,
            add_old_name_alias=True,
            alias_registry=registry,
            write=_G.get('write', True))
    except Exception:
        # process_one 안에서 못 잡은 예외. 통째로 남긴다.
        rec.update(status='error', stage='process_one',
                   detail=traceback.format_exc(limit=12))
        return rec

    if status != 'ok':
        # process_one이 삼킨 예외(repr만 남는다)도 그대로 기록한다.
        rec.update(status=status, stage='convert_body', detail=msg)
        return rec

    text = msg
    header = rp.build_rag_header(
        meta,
        extra_aliases=registry.aliases_for(meta.get('corp_code')),
        include_sector=False)
    body = text[len(header) + 2:] if text.startswith(header) else None

    base = os.path.basename(out_path)  # write=False 여도 경로는 계산된다
    stem = os.path.splitext(base)[0]
    # 파일명은 {corp_name}_{doc_id}[{suffix}] — corp_name에 '_'가 없다는
    # 보장이 없으므로 doc_id 위치를 문자열로 직접 찾는다.
    doc_id = meta.get('doc_id') or ''
    cut = stem.find('_' + doc_id)
    key = stem[cut + 1:] if cut >= 0 else stem

    rec.update(
        status='ok',
        out_file=base,
        key=key,
        bytes=len(text.encode('utf-8')),
        full_sha256=P.sha256_text(text),
        body_sha256=P.sha256_text(body) if body is not None else None,
        header_split_ok=body is not None,
    )
    return rec


# ══════════════════════════════════════════════════════════════════════
# 동의어 prescan 캐시
# ══════════════════════════════════════════════════════════════════════

def load_or_build_aliases(raw_root, manifest_path, force=False):
    """CompanyAliasRegistry.build()는 4,616개 원문 트리를 전부 세워
    수 분이 걸린다. 결과는 원문이 바뀌지 않는 한 불변이므로 캐시한다.
    (parser/ 코드는 손대지 않는다 — 결과 dict만 재사용한다.)"""
    import rag_pipeline
    n_xml = sum(len([f for f in fs if f.lower().endswith('.xml')])
                for _, _, fs in os.walk(raw_root))
    key = P.config_hash({'raw_root': raw_root, 'manifest': manifest_path,
                         'n_xml': n_xml, 'ver': 1})
    if not force and os.path.isfile(P.ALIAS_CACHE):
        with open(P.ALIAS_CACHE, encoding='utf-8') as f:
            cached = json.load(f)
        if cached.get('key') == key:
            print('동의어 캐시 사용: %s (회사 %d곳)'
                  % (P.ALIAS_CACHE, len(cached['by_corp'])))
            return cached['by_corp']

    print('동의어 prescan (원문 전량 훑음, 수 분 소요)...')
    t0 = time.time()
    index = rag_pipeline.ManifestIndex(manifest_path)
    reg = rag_pipeline.CompanyAliasRegistry()
    reg.build(raw_root, index, verbose=True)
    by_corp = {k: sorted(v) for k, v in reg.by_corp.items()}
    P.ensure_dirs(P.INTERIM_DIR)
    with open(P.ALIAS_CACHE, 'w', encoding='utf-8') as w:
        json.dump({'key': key, 'by_corp': by_corp}, w,
                  ensure_ascii=False, indent=1, sort_keys=True)
    print('  prescan 완료 %.1fs → %s' % (time.time() - t0, P.ALIAS_CACHE))
    return by_corp


# ══════════════════════════════════════════════════════════════════════
# 본체
# ══════════════════════════════════════════════════════════════════════

def collect_sources(raw_root):
    files = []
    for dirpath, _, filenames in os.walk(raw_root):
        for fn in filenames:
            if fn.lower().endswith('.xml'):
                files.append(os.path.join(dirpath, fn))
    files.sort()
    return files


def missing_source_records(manifest_path, seen_doc_ids):
    """XML 원문이 아예 없어 변환 대상에서 빠진 문서를 '없음'으로 기록한다.

    조용히 사라지면 4,204 대비 결손이 눈에 안 보인다. 규칙 2에 따라
    '처리하지 않았다는 사실'을 산출물에 남긴다."""
    out = []
    with open(manifest_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row['doc_id'] in seen_doc_ids:
                continue
            out.append({
                'doc_id': row['doc_id'],
                'doc_group': row.get('doc_group'),
                'corp_code': row.get('corp_code'),
                'corp_name': row.get('corp_name'),
                'source_path': row.get('file_path'),
                'status': 'no_source_xml',
                'stage': 'collect',
                'detail': 'XML 원문 없음 (file_format=%s) — 변환 대상 아님'
                          % row.get('file_format'),
            })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='0단계 회귀 베이스라인 동결')
    ap.add_argument('--raw-root', default=P.RAW_ROOT)
    ap.add_argument('--manifest', default=P.MANIFEST_PATH)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--jobs', type=int, default=min(61, max(1, (os.cpu_count() or 4) - 1)))  # 61: 윈도우 ProcessPoolExecutor 상한
    ap.add_argument('--keep-empty', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='기존 baseline/ 을 지우고 처음부터 다시 만든다')
    ap.add_argument('--rebuild-aliases', action='store_true',
                    help='동의어 prescan 캐시를 버리고 다시 훑는다 (~23분). '
                         '--force 와 별개다 — 베이스라인을 다시 만든다고 '
                         '원문이 바뀌는 건 아니기 때문.')
    a = ap.parse_args(argv)

    if a.force:
        for d in (P.BASELINE_DIR, P.BASELINE_MD_DIR):
            if os.path.isdir(d):
                print('--force: 기존 베이스라인 삭제 %s' % d)
                shutil.rmtree(d)
    P.ensure_dirs(P.BASELINE_MD_DIR, P.BASELINE_HASH_DIR, P.INTERIM_DIR)

    alias_by_corp = load_or_build_aliases(a.raw_root, a.manifest,
                                          force=a.rebuild_aliases)

    files = collect_sources(a.raw_root)
    if a.limit:
        files = files[:a.limit]
    print('원문 %d개 → 변환 시작 (jobs=%d)' % (len(files), a.jobs))

    t0 = time.time()
    records = []
    init_args = (a.manifest, alias_by_corp, not a.keep_empty)
    if a.jobs <= 1:
        _worker_init(*init_args)
        for i, path in enumerate(files, 1):
            records.append(_convert(path))
            if i % 200 == 0:
                print('  ... %d/%d  (%.0fs)' % (i, len(files), time.time() - t0))
    else:
        with ProcessPoolExecutor(max_workers=a.jobs,
                                 initializer=_worker_init,
                                 initargs=init_args) as ex:
            for i, rec in enumerate(ex.map(_convert, files, chunksize=4), 1):
                records.append(rec)
                if i % 200 == 0:
                    print('  ... %d/%d  (%.0fs)' % (i, len(files),
                                                    time.time() - t0))

    if not a.limit:
        # --limit 미리보기에서는 '안 돈 문서'를 결손으로 기록하면 안 된다.
        # 결손 기록은 전량 실행일 때만 의미가 있다.
        seen = set(r['doc_id'] for r in records if r.get('doc_id'))
        records.extend(missing_source_records(a.manifest, seen))
    records.sort(key=lambda r: (r.get('doc_id') or '', r.get('source_path') or ''))

    # ── 해시 동결 ──────────────────────────────────────────────────
    n_hash = 0
    for r in records:
        if r['status'] != 'ok':
            continue
        hp = os.path.join(P.BASELINE_HASH_DIR, r['key'] + '.md.sha256')
        with open(hp, 'w', encoding='utf-8') as w:
            w.write('%s  full\n%s  body\n' % (r['full_sha256'], r['body_sha256']))
        n_hash += 1

    with open(P.BASELINE_INDEX, 'w', encoding='utf-8') as w:
        for r in records:
            if r['status'] == 'ok':
                w.write(json.dumps(r, ensure_ascii=False) + '\n')

    with open(P.PARSE_REPORT, 'w', encoding='utf-8') as w:
        for r in records:
            w.write(json.dumps(r, ensure_ascii=False) + '\n')

    # ── 요약: 진행률이 아니라 실패 목록 ────────────────────────────
    by_status = {}
    for r in records:
        by_status[r['status']] = by_status.get(r['status'], 0) + 1
    docs_ok = len(set(r['doc_id'] for r in records
                      if r['status'] == 'ok' and r.get('doc_id')))

    print('')
    print('─' * 66)
    print('경과 %.1fs' % (time.time() - t0))
    for k in sorted(by_status):
        print('  %-16s %d' % (k, by_status[k]))
    print('  %-16s %d' % ('md 파일', n_hash))
    print('  %-16s %d / 4204' % ('doc_id 커버', docs_ok))
    print('  %-16s %d' % ('header 분리 실패', sum(
        1 for r in records if r['status'] == 'ok' and not r['header_split_ok'])))

    bad = [r for r in records if r['status'] not in ('ok', 'no_source_xml')]
    if bad:
        print('')
        print('실패 목록 (%d건):' % len(bad))
        for r in bad:
            last = (r.get('detail') or '').strip().splitlines()
            print('  [%s] %s  %s' % (r['status'],
                                     r.get('doc_id') or r['source_path'],
                                     last[-1] if last else ''))
    nos = [r for r in records if r['status'] == 'no_source_xml']
    if nos:
        print('')
        print('XML 원문 없음 (%d건, 기대된 결손 — parse_report에 기록됨):' % len(nos))
        for r in nos:
            print('  %s  %s' % (r['doc_id'], r['detail']))

    print('')
    print('베이스라인: %s' % P.BASELINE_DIR)
    print('처리 기록  : %s' % P.PARSE_REPORT)
    return 0 if not bad else 2


if __name__ == '__main__':
    sys.exit(main())

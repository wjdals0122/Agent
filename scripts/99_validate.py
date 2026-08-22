# -*- coding: utf-8 -*-
"""검증 골든셋 — 6종을 돌리고 reports/에 CSV를 쓴다.

    --baseline   서술 md 해시가 0단계와 동일한가          (0단계 완료 조건)
    --docjson    doc.json 을 거쳐 렌더해도 동일한가        (3단계 완료 조건)
    --sanitize   이스케이프 횟수 × 4 = 문자수 증가분      (2단계 이후)
    --encoding   문서별 한글 음절 비율 >= 5%
    --structure  //SECTION-2 개수 = 순회 도달 개수
    --grid       표별 열 수 단일값 (ragged 0)
    --sums       {XBRL} 표의 총계 행 정합                 (5단계 이후)
    --facts      구조화 팩트가 원문에 근거하는가          (환각 없음)
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
import re
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
# 8. facts — 구조화 팩트가 원문에 근거하는가 (환각 없음 최소판)
# ══════════════════════════════════════════════════════════════════════

_RE_ISO_TAIL = re.compile(
    r'\s*\((\d{4}-\d{2}-\d{2}(?: ~ \d{4}-\d{2}-\d{2})?)\)\s*$')


def _chunk_value_set(doc):
    """조각에 실제로 실린 글자 조각 전부.

    렌더러가 한글 날짜 뒤에 ISO 를 덧붙이므로('2024년 12월 09일 (2024-12-09)')
    그 꼬리를 뗀 형태도 같이 넣는다. 안 그러면 멀쩡한 값이 근거 없음으로
    잡힌다 — 실측에서 2.4% 가 전부 이 경우였다.
    """
    S = set()

    def add(x):
        if not x:
            return
        S.add(x)
        y = _RE_ISO_TAIL.sub('', x)
        if y != x:
            S.add(y)

    for c in doc.get('chunks') or []:
        k = c[0]
        if k == 'h':
            add(c[2])
        elif k == 'p':
            add(c[1])
        elif k == 'kv':
            for q in c[1]:
                add(q)
            add(c[2])
        elif k == 't':
            for q in c[1]:
                add(q)
            for r in c[2]:
                for q in r:
                    add(q)
    return S


def check_facts(args):
    """구조화 팩트의 값이 산출물(조각)에 실제로 존재하는가.

    이 검사가 답하는 질문은 **"없는 값을 지어내지 않았는가"** 하나다.
    답하지 않는 질문도 분명히 해 둔다 — 원문의 모든 값을 빠짐없이
    담았는가(완전성)는 이 검사로 알 수 없다. 그건 별개의 작업이다.

    판정은 문서 단위다. 한 문서의 팩트 중 근거를 못 찾은 것이
    임계(기본 1%)를 넘으면 FAIL. 개별 값 하나는 정규화 차이로 어긋날 수
    있지만, 한 문서에서 여러 건이 어긋나면 그건 추출이 틀린 것이다.
    """
    if not _need_docs():
        return 3, []

    floor = 0.01
    sys.path.insert(0, os.path.join(P.REPO_ROOT, 'src'))
    from normalize.value import is_empty_value

    rows = []
    tot = hit = skipped = 0
    t0 = time.time()
    for doc_id, group, part in _iter_parts(args):
        doc = part.get('doc') or {}
        st = doc.get('structured') or {}
        facts = st.get('acode_facts') or []
        if not facts:
            continue
        S = _chunk_value_set(doc)
        blob = None
        n = ok = 0
        sample = []
        for f in facts:
            v = (f.get('value') or '').strip()
            if not v:
                continue
            # `-` 같은 빈 값은 drop_empty 정책이 md 에서 **의도적으로**
            # 없앤다. 조각에 없는 게 정상이므로 근거 대조 대상이 아니다.
            # 이걸 세면 정책이 지운 것을 조작으로 오해하게 된다.
            if is_empty_value(v):
                skipped += 1
                continue
            n += 1
            if v in S:
                ok += 1
                continue
            # 셀이 합쳐져 렌더된 경우가 있다(중첩 표). 정확 일치가 아니어도
            # 산출물 어딘가에 글자가 들어 있으면 지어낸 값은 아니다.
            if blob is None:
                blob = ' ␟ '.join(S)
            if v in blob:
                ok += 1
                continue
            if len(sample) < 3:
                sample.append('%s=%s' % (f.get('acode'), v[:30]))
        if not n:
            continue
        tot += n
        hit += ok
        bad = n - ok
        ratio = bad / n
        rows.append(dict(
            doc_id=doc_id, doc_group=group,
            source_path=part.get('source_path'),
            result='PASS' if ratio <= floor else 'FAIL',
            body_same='', header_same='',
            detail='' if ratio <= floor else
                   '팩트 %d개 중 %d개(%.1f%%) 근거 못 찾음: %s'
                   % (n, bad, 100 * ratio, ' / '.join(sample))))

    print('  팩트 %s개 중 근거 확인 %s개 (%.3f%%) / %.1fs'
          % ('{:,}'.format(tot), '{:,}'.format(hit),
             100 * hit / max(1, tot), time.time() - t0))
    print('  빈 값(-) %s개는 drop_empty 정책이 md 에서 지운 것이라 대조 제외'
          % '{:,}'.format(skipped))
    print('  이 검사는 "지어내지 않았는가"만 본다. '
          '"빠짐없이 담았는가"(완전성)는 답하지 않는다.')
    if not rows:
        rows.append(dict(result='PASS', doc_id='(전체)', doc_group='',
                         source_path='', body_same='', header_same='',
                         detail='구조화 팩트 0건'))
    ok = sum(1 for r in rows if r['result'] == 'PASS')
    return (0 if ok == len(rows) else 2), rows


# ══════════════════════════════════════════════════════════════════════
# 6. sums — {XBRL} 표의 총계 행 정합
# ══════════════════════════════════════════════════════════════════════

def check_sums(args):
    """총계 = 부분합 이 맞는가.

    불일치는 rowspan 복제나 헤더밴드 판정 오류의 신호다. 그런 표는
    parse_confidence=low 로 강등돼 facts_* 진입이 막힌다 —
    **잘못된 숫자를 확신 있게 답하는 경로를 구조적으로 차단**하는 장치다.

    그래서 이 검사의 PASS 기준은 "불일치 0" 이 아니라
    "불일치가 전부 low 로 강등됐는가" 다. 강등 없이 통과한 불일치가
    하나라도 있으면 FAIL.
    """
    import gzip
    if not os.path.isdir(P.INTERIM_DOCS_DIR):
        print('doc.json 이 없다. scripts/03_build_docjson.py 를 먼저.')
        return 3, []

    files = sorted(f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz'))
    if args.limit:
        files = files[:args.limit]

    rows = []
    n_groups = n_checked = n_bad = n_low = 0
    t0 = time.time()
    for i, fn in enumerate(files, 1):
        with gzip.open(os.path.join(P.INTERIM_DOCS_DIR, fn), 'rt',
                       encoding='utf-8') as f:
            payload = json.load(f)
        for part in payload.get('parts') or []:
            st = (part.get('doc') or {}).get('structured') or {}
            for g in st.get('financials') or []:
                n_groups += 1
                checks = g.get('sum_checks') or []
                bad = [c for c in checks if not c['ok']]
                n_checked += len(checks)
                low = g.get('parse_confidence') == 'low'
                if low:
                    n_low += 1
                if not bad:
                    continue
                n_bad += len(bad)
                # 불일치가 있는데 강등이 안 됐으면 그게 진짜 사고다
                ok = low
                worst = max(bad, key=lambda c: abs(c['diff']))
                rows.append(dict(
                    doc_id=payload['doc_id'], doc_group=payload.get('doc_group'),
                    source_path=part.get('source_path'),
                    result='PASS' if ok else 'FAIL',
                    body_same='', header_same='',
                    detail='%s 불일치 %d건 (최대 차 %s, %s) conf=%s'
                           % (g['aclass'], len(bad), '{:,}'.format(worst['diff']),
                              worst['check'][:60], g['parse_confidence'])))
        if i % 1000 == 0:
            print('  ... %d/%d (%.0fs)' % (i, len(files), time.time() - t0))

    print('')
    print('{XBRL} 그룹 %s개 / 총계 검사 %s건 / 불일치 %s건 / low 강등 %s개'
          % ('{:,}'.format(n_groups), '{:,}'.format(n_checked),
             '{:,}'.format(n_bad), '{:,}'.format(n_low)))
    if not rows:
        rows.append(dict(result='PASS', doc_id='(전체)',
                         detail='총계 불일치 0건', body_same='', header_same='',
                         source_path=''))
    ok = sum(1 for r in rows if r['result'] == 'PASS')
    return (0 if ok == len(rows) else 2), rows


# ══════════════════════════════════════════════════════════════════════
# 2~5. sanitize / encoding / structure / grid — doc.json 진단값 기반
# ══════════════════════════════════════════════════════════════════════

def _iter_parts(args):
    """doc.json 을 돌며 (doc_id, doc_group, part) 를 준다."""
    import gzip
    files = sorted(f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz'))
    if args.limit:
        files = files[:args.limit]
    for fn in files:
        with gzip.open(os.path.join(P.INTERIM_DOCS_DIR, fn), 'rt',
                       encoding='utf-8') as f:
            payload = json.load(f)
        for part in payload.get('parts') or []:
            yield payload['doc_id'], payload.get('doc_group'), part


def _need_docs():
    if not os.path.isdir(P.INTERIM_DOCS_DIR):
        print('doc.json 이 없다. scripts/03_build_docjson.py 를 먼저.')
        return False
    return True


def _diag_check(args, name, fn, note=None):
    """진단값 하나로 판정하는 검사들의 공통 뼈대."""
    if not _need_docs():
        return 3, []
    rows = []
    n = 0
    t0 = time.time()
    for doc_id, group, part in _iter_parts(args):
        d = (part.get('doc') or {}).get('diagnostics')
        if d is None:
            rows.append(dict(doc_id=doc_id, doc_group=group,
                             source_path=part.get('source_path'),
                             result='FAIL', body_same='', header_same='',
                             detail='진단값 없음 — doc.json 이 옛 버전이다'))
            continue
        n += 1
        res = fn(d, group, part)
        if res is None:
            continue                      # 이 검사 대상이 아님
        ok, detail = res
        rows.append(dict(doc_id=doc_id, doc_group=group,
                         source_path=part.get('source_path'),
                         result='PASS' if ok else 'FAIL',
                         body_same='', header_same='', detail=detail))
    print('  진단값 있는 part %s개 / 판정 %s건 / %.1fs'
          % ('{:,}'.format(n), '{:,}'.format(len(rows)), time.time() - t0))
    if note:
        print('  %s' % note)
    if not rows:
        rows.append(dict(result='PASS', doc_id='(전체)', doc_group='',
                         source_path='', body_same='', header_same='',
                         detail='판정 대상 0건'))
    ok = sum(1 for r in rows if r['result'] == 'PASS')
    return (0 if ok == len(rows) else 2), rows


def check_sanitize(args):
    """이스케이프 횟수 x 4 = 문자수 증가분.

    `&` 1자 -> `&amp;` 5자 이므로 치환 1회당 4자가 는다.

    지금 정책(config/exception_policy.yaml)은 E1/E2 를 count_only 로
    둔다 — 기존 관대 파서가 이미 처리하고 있어 치환을 얹으면 이중 처리가
    되기 때문이다. 그래서 치환은 0건이고 이 검사는 아직 잡을 것이 없다.
    그 사실을 조용히 통과시키지 않고 명시한다. 정책을 escape 로 바꾸면
    이 검사가 그때부터 진짜로 일한다.
    """
    def one(d, group, part):
        ops = d.get('escape_ops', 0)
        cnt = d.get('escape_count', 0)
        add = d.get('escape_chars_added', 0)
        if not ops:
            return None
        want = cnt * 4
        return (add == want,
                '치환 %d회 -> 증가 %d자 (기대 %d자)' % (cnt, add, want))

    return _diag_check(
        args, 'sanitize', one,
        note='정책이 count_only 라 치환 0건 — 검사는 살아 있고 대상이 없다.')


def check_encoding(args):
    """문서별 한글 음절 비율 >= 5%.

    분모는 태그를 지운 원문이 아니라 파서가 실제로 읽어낸 본문이다.
    1단계에서 확인: 태그 제거 정규식은 DSD 의 긴 속성 때문에 분모를
    부풀려, 한글 음절이 198,955개나 되는 멀쩡한 13.7MB 문서를 2.84%로
    만들었다. 그 방식이었으면 이 검사가 멀쩡한 문서를 떨어뜨린다.
    """
    floor = 0.05

    def one(d, group, part):
        r = d.get('hangul_ratio')
        if r is None or not d.get('text_chars'):
            return None
        if r >= floor:
            return (True, '')
        return (False, '한글 비율 %.4f < %.2f (음절 %s / 글자 %s)'
                % (r, floor, '{:,}'.format(d.get('hangul', 0)),
                   '{:,}'.format(d.get('text_chars', 0))))
    return _diag_check(args, 'encoding', one)


def check_structure(args):
    """//SECTION-2 개수 = 순회 도달 개수.

    E4 를 지키는 장치다. 기존 순회는 descendant 축(catch-all 재귀)이라
    LIBRARY 컨테이너를 그냥 통과한다 — 의도가 아니라 부수효과다.
    grep -c LIBRARY parser/*.py -> major 1, holding 0, periodic 0 인데
    실제 LIBRARY 노드는 29,339개다. 순회를 "모르는 태그는 건너뛴다"로
    바꾸면 조용히 깨지고, 바꾼 사람은 자기가 무엇을 껐는지 알 수 없다.
    이 검사가 그걸 잡는다.
    """
    def one(d, group, part):
        if d.get('tree_scan') != 'dsd_xml':
            return None                    # exchange 는 HTML — 대상 아님
        a, b = d.get('section2_regex'), d.get('section2_reached')
        if a is None or b is None:
            return None
        if a == b:
            return (True, '')
        return (False,
                '//SECTION-2 %d개인데 순회 도달 %d개 (유실 %d, LIBRARY %d)'
                % (a, b, a - b, d.get('library_nodes', 0)))
    return _diag_check(args, 'structure', one)


def _grid_one(d, group, part):
    if d.get('tree_scan') != 'dsd_xml':
        return None
    n = d.get('tables_ragged')
    if n is None:
        return None
    if n == 0:
        return (True, '')
    return (False, '표 %s개 중 ragged %d개'
            % ('{:,}'.format(d.get('tables', 0)), n))


def _grid_property_test():
    """expand 가 어떤 rowspan/colspan 조합에서도 직사각형을 내는가."""
    sys.path.insert(0, os.path.join(P.REPO_ROOT, 'src'))
    from normalize import grid as G

    class C:
        def __init__(self, rs, cs):
            self.rowspan, self.colspan = rs, cs
            self.text = '%dx%d' % (rs, cs)

    def make(spec):
        return [[C(rs, cs) for rs, cs in row] for row in spec]

    cases = {
        '단순 2x3': [[(1, 1)] * 3, [(1, 1)] * 3],
        'rowspan 23': [[(23, 1), (1, 1)]] + [[(1, 1)] for _ in range(22)],
        '한 행에 rowspan 6개': [[(3, 1)] * 6] + [[(1, 1)] * 6] * 2,
        'rowspan+colspan 동시': [[(2, 2), (1, 1)], [(1, 1)],
                                 [(1, 1), (1, 1), (1, 1)]],
        'colspan 큰 값': [[(1, 5)], [(1, 1)] * 5],
        '빈 표': [],
    }
    bad = []
    for name, spec in cases.items():
        g, maxc = G.expand(make(spec), lambda n, r: n)
        if g and len(set(len(r) for r in g)) > 1:
            bad.append('%s: 열 수 %s' % (name, sorted(set(len(r) for r in g))))
        if g and maxc != len(g[0]):
            bad.append('%s: maxc %d != 실제 %d' % (name, maxc, len(g[0])))
    if bad:
        return False, '; '.join(bad)
    return True, '%d개 조합 전부 직사각형' % len(cases)


def check_grid(args):
    """표별 열 수 단일값 (ragged 0).

    normalize.grid.expand 가 끝에서 모든 행을 maxc 로 채우므로 정상
    경로에서는 항상 0이어야 한다. 0이 아니면 격자를 만든 쪽이 틀린 것이고,
    그건 rowspan 복제 오류로 이어진다.

    doc.json 전수 판정에 더해 expand 자체의 성질 검사도 같이 돌린다 —
    실측 극단값(rowspan 최대 23, 한 행에 rowspan 6개, rowspan+colspan
    동시 3,507군데)을 만들어 넣어 본다.
    """
    code, rows = _diag_check(args, 'grid', _grid_one)
    prop_ok, prop_detail = _grid_property_test()
    rows.append(dict(doc_id='(expand 성질검사)', doc_group='', source_path='',
                     result='PASS' if prop_ok else 'FAIL',
                     body_same='', header_same='', detail=prop_detail))
    print('  expand 성질검사: %s' % prop_detail)
    ok = sum(1 for r in rows if r['result'] == 'PASS')
    return (0 if ok == len(rows) else 2), rows



CHECKS = {
    'baseline': check_baseline,
    'docjson': check_docjson,
    'sanitize': check_sanitize,
    'encoding': check_encoding,
    'structure': check_structure,
    'grid': check_grid,
    'sums': check_sums,
    'facts': check_facts,
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

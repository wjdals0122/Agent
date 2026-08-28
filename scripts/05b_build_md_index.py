# -*- coding: utf-8 -*-
"""5b단계 — md 파일 단위 메타 인덱스.

    data/index/md_files.jsonl      md 파일 하나당 한 줄 (4,616줄)

왜 05(문서 단위)로 부족한가
────────────────────────────────────────────────────────────────────────
`data/index/documents.jsonl` 은 **문서 단위**(4,201)다. 그런데 청킹의 입력은
**md 파일 단위**(4,616)다. 정기보고서는 본문·첨부로 쪼개져 한 문서가 md 여러
개로 나오기 때문이다(분할본 415개).

md 파일을 손에 들고 "이건 어느 회사 무슨 공시인가"를 물으면 지금은 답할
곳이 없다. `baseline/index.jsonl` 은 md 파일 단위지만 해시·바이트 말고는
얇고(회사명·문서군까지), `documents.jsonl` 은 두껍지만 문서 단위다.
이 파일은 **그 둘을 md 파일 단위로 조인한 것**이다.

값을 여기서 새로 만들지 않는다(05와 같은 규칙). 예외는 둘뿐이고 둘 다
근거가 있다.

    document_title  = "[{corp_name}] {report_nm}"
        배포본 청크(disclosure_chunks_by_10_companies)의 document_title
        계약을 그대로 재현한다. 404문서 대조 불일치 0건.
    is_part / part_suffix
        baseline 의 key 가 doc_id 로 시작하는지로 갈린다. 4,616건 예외 0.

출처
────────────────────────────────────────────────────────────────────────
    baseline/index.jsonl        md 파일 단위 — 해시·바이트·원문경로
    data/index/documents.jsonl  문서 단위   — 업종·섹터·기수·dart_url
    corpus/manifest.jsonl       문서 단위   — 사업연도(base_year)·file_format
    data/interim/alias_registry.json        회사명 별칭

    python scripts/05b_build_md_index.py
    python scripts/05b_build_md_index.py --verify    # 출처와 전량 재대조
"""
import argparse
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

OUT = os.path.join(P.INDEX_DIR, 'md_files.jsonl')


def load_jsonl(path, key):
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[r[key]] = r
    return out


def build_rows():
    docs = load_jsonl(os.path.join(P.INDEX_DIR, 'documents.jsonl'), 'doc_id')
    man = load_jsonl(P.MANIFEST_PATH, 'doc_id')
    with open(P.ALIAS_CACHE, encoding='utf-8') as f:
        alias = json.load(f).get('by_corp', {})

    rows = []
    orphans = []
    with open(P.BASELINE_INDEX, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            b = json.loads(line)
            did = b['doc_id']
            d = docs.get(did)
            m = man.get(did, {})
            if d is None:
                orphans.append(b['key'])
                continue

            key = b['key']
            is_part = key != did
            corp = d['corp_name']

            rows.append({
                # ── md 파일 자신 ──
                'md_file': b['out_file'],
                'key': key,
                'doc_id': did,
                'is_part': is_part,
                'part_suffix': key[len(did) + 1:] if is_part else None,
                'n_parts': d.get('n_parts'),
                'source_path': b['source_path'],
                'md_bytes': b['bytes'],
                'full_sha256': b['full_sha256'],
                'body_sha256': b['body_sha256'],
                'header_split_ok': b['header_split_ok'],

                # ── 회사 ──
                'company': corp,
                'company_aliases': alias.get(d['corp_code'], []),
                'corp_code': d['corp_code'],      # DART 고유번호 8자리
                'stock_code': d['stock_code'],    # 종목코드 6자리
                'industry': d['industry'],
                'sector': d['sector'],

                # ── 공시 ──
                'disclosure_type': d['doc_group'],   # periodic/exchange/major/holding
                'doc_subtype': d.get('doc_subtype'),
                'report_nm': d['report_nm'],
                'document_title': '[%s] %s' % (corp, d['report_nm']),   # 파생
                'is_correction': d['is_correction'],
                'receipt_no': d['rcept_no'],
                'rcept_dt': d['rcept_dt'],
                'base_year': m.get('base_year'),     # 사업연도. 접수연도가 아니다
                'base_month': m.get('base_month'),
                'dart_url': d['dart_url'],
                'file_format': m.get('file_format'),

                # ── 내용 ──
                'n_blocks': d.get('n_blocks'),
                'n_tables': d.get('n_tables'),
                'toc': d.get('toc', []),
                'periods': d.get('periods', {}),
                'periods_unresolved': d.get('periods_unresolved'),
                'parse_confidence': d.get('parse_confidence'),
                'status': d['status'],
            })
    return rows, orphans


def verify(rows):
    """출처와 전량 재대조. 여기서 값을 지어냈으면 잡힌다."""
    docs = load_jsonl(os.path.join(P.INDEX_DIR, 'documents.jsonl'), 'doc_id')
    man = load_jsonl(P.MANIFEST_PATH, 'doc_id')
    base = load_jsonl(P.BASELINE_INDEX, 'key')
    bad = []
    for r in rows:
        b = base.get(r['key'])
        d = docs.get(r['doc_id'])
        m = man.get(r['doc_id'], {})
        if b is None or d is None:
            bad.append((r['key'], 'source_missing'))
            continue
        for fld, src in (('md_bytes', b['bytes']),
                         ('full_sha256', b['full_sha256']),
                         ('body_sha256', b['body_sha256']),
                         ('source_path', b['source_path']),
                         ('company', d['corp_name']),
                         ('stock_code', d['stock_code']),
                         ('disclosure_type', d['doc_group']),
                         ('receipt_no', d['rcept_no']),
                         ('rcept_dt', d['rcept_dt']),
                         ('dart_url', d['dart_url']),
                         ('base_year', m.get('base_year'))):
            if r[fld] != src:
                bad.append((r['key'], fld))
        if r['document_title'] != '[%s] %s' % (d['corp_name'], d['report_nm']):
            bad.append((r['key'], 'document_title'))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true',
                    help='기존 출력을 출처와 전량 재대조만 하고 끝낸다')
    a = ap.parse_args(argv)

    if a.verify:
        if not os.path.isfile(OUT):
            print('%s 가 없다. 먼저 인자 없이 실행할 것.' % P.rel(OUT))
            return 3
        with open(OUT, encoding='utf-8') as f:
            rows = [json.loads(l) for l in f if l.strip()]
        bad = verify(rows)
        print('대조 %d줄' % len(rows))
        if bad:
            print('불일치 %d건' % len(bad))
            for k, fld in bad[:20]:
                print('   %s  %s' % (k, fld))
            return 2
        print('불일치 0건 — 이 파일은 출처를 옮겨 적기만 했다.')
        return 0

    P.ensure_dirs(P.INDEX_DIR)
    rows, orphans = build_rows()
    with open(OUT, 'w', encoding='utf-8') as w:
        for r in rows:
            w.write(json.dumps(r, ensure_ascii=False) + '\n')

    n_part = sum(1 for r in rows if r['is_part'])
    docs_covered = len(set(r['doc_id'] for r in rows))
    by_group = {}
    for r in rows:
        by_group[r['disclosure_type']] = by_group.get(r['disclosure_type'], 0) + 1

    print('%s 기록' % P.rel(OUT))
    print('  md 파일   %d  (분할본 %d)' % (len(rows), n_part))
    print('  문서      %d' % docs_covered)
    for k in sorted(by_group):
        print('  %-10s %d' % (k, by_group[k]))
    if orphans:
        print('  ⚠ documents.jsonl 에 없는 md %d건: %s'
              % (len(orphans), orphans[:5]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

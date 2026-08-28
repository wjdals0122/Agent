# -*- coding: utf-8 -*-
"""본문 손실 검증 — 원문의 글자가 산출물에 다 들어갔는가.

`facts` 검사가 답하지 않는 쪽이다.
    facts     지어내지 않았는가   (산출물 → 원문)
    coverage  빠뜨리지 않았는가   (원문 → 산출물)   ← 이 파일

PARSING_NOTES 기준 미실시 영역
    본문 손실 3중 검증: holding·periodic 만 실시. **exchange·major 미실시.**
    환각 검증: 네 문서군 전부 미실시.
그래서 네 문서군을 같은 방법으로 한 번에 잰다.

방법
    원문 쪽 : 파서가 세운 트리의 텍스트(= 파서가 실제로 본 글자)
    산출물 쪽: doc.json 의 chunks 에 실린 글자
    비교    : 3자 이상 한글/영숫자 토큰의 집합 차이

3자 이상으로 자르는 이유는 조사·기호가 정규화 과정에서 붙었다 떨어졌다
하기 때문이다. 낱말 단위로 봐야 '내용이 사라졌는가' 를 본다.

의도적으로 빠지는 것 (손실이 아니다)
    · `_IGNORE` 태그 — SUMMARY(메타데이터), DOCUMENT-NAME, FORMULA-VERSION
    · IMAGE 안의 파일명
    · drop_empty 정책이 지우는 '-' 값
    이것들은 파서가 일부러 뺀 것이라 손실로 세면 안 된다. 원문 토큰에서
    미리 걷어내고 비교한다.

    python scripts/07_coverage.py --jobs 10
    python scripts/07_coverage.py --sample 200
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
sys.path.insert(0, P.PARSER_DIR)

OUT = os.path.join(P.REPORTS_DIR, 'coverage.jsonl')

RE_TOKEN = re.compile(r'[가-힣]{3,}|[A-Za-z0-9][A-Za-z0-9.\-]{2,}')

_G = {}


def _init(manifest_path):
    import rag_pipeline
    _G['index'] = rag_pipeline.ManifestIndex(manifest_path)
    _G['mods'] = {
        'exchange': rag_pipeline.exchange_parser,
        'major': rag_pipeline.major_parser,
        'holding': rag_pipeline.holding_parser,
        'periodic': rag_pipeline.periodic_parser,
    }


def _tokens(s):
    return set(RE_TOKEN.findall(s or ''))


def _chunk_text(doc):
    out = []
    for c in doc.get('chunks') or []:
        k = c[0]
        if k == 'h':
            out.append(c[2])
        elif k == 'p':
            out.append(c[1])
        elif k == 'kv':
            out.extend(list(c[1]))
            if c[2]:
                out.append(c[2])
        elif k == 't':
            out.extend(c[1])
            for r in c[2]:
                out.extend(r)
    return ' '.join(x for x in out if x)


def _source_text(mod, group, source):
    """파서가 실제로 본 글자. 의도적으로 버리는 태그는 걷어낸다."""
    if group == 'exchange':
        # exchange 는 자기 트리를 쓴다.
        # ⚠ _inner_html(root) 를 그대로 쓰면 <style> 안의 CSS 까지 원문
        #   텍스트로 센다. 실측에서 손실 37.95% 로 나왔는데 누락 토큰이
        #   전부 008BE3 / 0px / 13pt 였다 — 본문이 아니라 스타일시트다.
        #   화면 전용 노드는 빼야 '본문을 빠뜨렸는가' 를 잰다.
        b = mod._TreeBuilder()
        b.feed(source)
        skip = {'style', 'script', 'head', 'meta', 'link', 'title'}
        parts = []

        def rec(n):
            for c in n.children:
                if (c.tag or '').lower() in skip:
                    continue
                parts.append(''.join(r for r in c.raw
                                     if isinstance(r, str)))
                rec(c)

        rec(b.root)
        return mod.clean(' '.join(parts))

    from normalize import tree
    b = mod._TreeBuilder()
    b.feed(source)

    drop = set(tree.IGNORE) | {'IMAGE', 'IMG', 'IMG-CAPTION'}

    # ⚠ 노드마다 글자를 끊어 이어붙이면 안 된다. 원문이 '시프트업' 을
    #   <SPAN>시</SPAN>프트업 처럼 쪼개 놓으면 '프트업' 이라는 없는 낱말이
    #   생기고, 그게 산출물에 없으니 손실로 잡힌다 — 실측에서 periodic 의
    #   누락 표본이 '프트업'·'딩속도'·'포함하' 같은 조각이었다.
    #   **파서와 같은 방식으로** 재귀 연결해야 낱말이 보존된다.
    def rec(n):
        out = []
        for r in n.raw:
            if isinstance(r, str):
                out.append(r)
            elif r.tag not in drop:
                out.append(rec(r))
        return ''.join(out)

    return rec(b.root)


def _one(job):
    doc_id, docpath, keep_empty = job
    with gzip.open(docpath, 'rt', encoding='utf-8') as f:
        payload = json.load(f)
    group = payload.get('doc_group')
    mod = _G['mods'].get(group)
    rec = {'doc_id': doc_id, 'doc_group': group}
    if mod is None:
        rec['status'] = 'unsupported'
        return rec

    src_t = set()
    out_t = set()
    for part in payload.get('parts') or []:
        path = os.path.join(P.REPO_ROOT, part['source_path'])
        path = P.on_disk(path) or path      # NFC 기록 ↔ NFD 디스크
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            source = mod.decode(raw)
            src_t |= _tokens(_source_text(mod, group, source))
        except Exception as e:
            rec['status'] = 'error'
            rec['detail'] = repr(e)
            return rec
        if keep_empty:
            # drop_empty 는 '값이 -' 인 항목을 지우는 **표시 정책**이지
            # 포착 실패가 아니다. 실측: exchange 손실 5.29% 가 전부 이것이고
            # keep_empty 로 다시 만들면 0.000% 가 된다. '다 담았는가' 를
            # 물으려면 정책을 끄고 비교해야 한다.
            import rag_pipeline as _rp
            _, d2 = _rp.convert_body(path, group,
                                     corp_name=payload.get('corp_name'),
                                     receipt_no=payload.get('rcept_no'),
                                     drop_empty=False)
            out_t |= _tokens(_chunk_text(d2))
        else:
            out_t |= _tokens(_chunk_text(part.get('doc') or {}))

    raw_missing = src_t - out_t

    # 경계 차이는 손실이 아니다.
    # 원문이 <SPAN>가. 사업의 개요</SPAN>당사의… 처럼 붙여 놓으면 원문 쪽
    # 토큰은 '개요당사의' 가 되지만, 파서는 _para_text 로 올바르게 띄워
    # '개요' + '당사의' 를 낸다. 내용은 다 있는데 토큰 경계만 다르다.
    # 그래서 쪼개서 양쪽 다 산출물에 있으면 손실로 세지 않는다.
    # 반대 방향의 경계 차이도 있다 — 파서가 **합치는** 경우.
    # 표지 콜론 라벨 `1. 정정대상 공시서류 :` 을 _colon_kv 가 공백·콜론을
    # 걷어 `1.정정대상공시서류` 한 덩어리로 만든다(PARSING_NOTES 가
    # "놓친 칸 6,349개는 전부 이 의도된 동작" 이라고 적은 그 로직).
    # 그러면 원문 토큰 '정정대상' 은 산출물 토큰 집합에 없지만 **다른
    # 토큰 안에 들어 있다.** 내용이 사라진 게 아니다.
    blob = ' ␟ '.join(out_t)

    missing = []
    boundary = 0
    for t in raw_missing:
        split_ok = False
        for i in range(3, len(t) - 2):
            if t[:i] in out_t and t[i:] in out_t:
                split_ok = True
                break
        if not split_ok and t in blob:
            split_ok = True          # 합쳐진 토큰 안에 그대로 들어 있다
        if split_ok:
            boundary += 1
        else:
            missing.append(t)

    rec.update(
        status='ok',
        source_tokens=len(src_t),
        output_tokens=len(out_t),
        missing=len(missing),
        boundary_only=boundary,
        loss=round(len(missing) / len(src_t), 6) if src_t else 0.0,
        samples=sorted(missing)[:8],
    )
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description='본문 손실 검증')
    ap.add_argument('--manifest', default=P.MANIFEST_PATH)
    ap.add_argument('--jobs', type=int, default=min(61, max(1, (os.cpu_count() or 4) - 1)))  # 61: 윈도우 ProcessPoolExecutor 상한
    ap.add_argument('--sample', type=int, default=0)
    ap.add_argument('--keep-empty', action='store_true',
                    help='drop_empty 를 끄고 비교한다 (원문 포착률의 정직한 값)')
    a = ap.parse_args(argv)

    files = sorted(f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz'))
    jobs = [(f[:-8], os.path.join(P.INTERIM_DOCS_DIR, f), a.keep_empty)
            for f in files]
    if a.sample:
        step = max(1, len(jobs) // a.sample)
        jobs = jobs[::step][:a.sample]
    print('문서 %d개 검사 (jobs=%d)%s'
          % (len(jobs), a.jobs, ' [keep-empty]' if a.keep_empty else ''))

    P.ensure_dirs(P.REPORTS_DIR)
    t0 = time.time()
    recs = []
    if a.jobs <= 1:
        _init(a.manifest)
        for i, j in enumerate(jobs, 1):
            recs.append(_one(j))
            if i % 200 == 0:
                print('  ... %d/%d (%.0fs)' % (i, len(jobs), time.time() - t0))
    else:
        with ProcessPoolExecutor(max_workers=a.jobs, initializer=_init,
                                 initargs=(a.manifest,)) as ex:
            for i, r in enumerate(ex.map(_one, jobs, chunksize=4), 1):
                recs.append(r)
                if i % 200 == 0:
                    print('  ... %d/%d (%.0fs)' % (i, len(jobs), time.time() - t0))

    with open(OUT, 'w', encoding='utf-8') as w:
        for r in recs:
            w.write(json.dumps(r, ensure_ascii=False) + '\n')

    ok = [r for r in recs if r['status'] == 'ok']
    import collections
    by = collections.defaultdict(list)
    for r in ok:
        by[r['doc_group']].append(r['loss'])

    print('')
    print('─' * 62)
    print('%-10s %8s %10s %10s %10s' % ('문서군', '문서', '평균손실', '중앙값', '최대'))
    for g in sorted(by):
        v = sorted(by[g])
        print('%-10s %8d %9.3f%% %9.3f%% %9.3f%%'
              % (g, len(v), 100 * sum(v) / len(v), 100 * v[len(v) // 2],
                 100 * v[-1]))
    allv = sorted(x for v in by.values() for x in v)
    if allv:
        print('%-10s %8d %9.3f%% %9.3f%% %9.3f%%'
              % ('전체', len(allv), 100 * sum(allv) / len(allv),
                 100 * allv[len(allv) // 2], 100 * allv[-1]))

    worst = sorted(ok, key=lambda r: -r['loss'])[:8]
    print('')
    print('손실 상위 8건:')
    for r in worst:
        print('  %-28s %6.2f%%  누락 %s  예: %s'
              % (r['doc_id'], 100 * r['loss'], '{:,}'.format(r['missing']),
                 ', '.join(r['samples'][:4])))
    bad = [r for r in recs if r['status'] != 'ok']
    if bad:
        print('')
        print('검사 실패 %d건' % len(bad))
    print('')
    print('원자료: %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())

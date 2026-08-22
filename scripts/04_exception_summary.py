# -*- coding: utf-8 -*-
"""4단계 — 예외 유형별 발생 건수 집계 → reports/exception_summary.md.

읽는 것
    config/exception_policy.yaml       정책 (무엇을 잡기로 했나)
    data/interim/docs/*.json.gz        실제 조치 기록 (무엇을 잡았나)
    data/interim/parse_report.jsonl    문서 단위 처리 결과

이 리포트가 답해야 하는 질문은 하나다 —
**"정책에 적어 놓고 실제로는 안 도는 규칙이 있는가?"**
정책에 있는데 기록이 0건이면 둘 중 하나다: 이 코퍼스에 정말 없거나,
규칙이 안 돌고 있거나. 둘은 완전히 다른 사실이라 구분해서 적는다.

    python scripts/04_exception_summary.py
"""
import gzip
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(P.REPO_ROOT, 'src'))

OUT = os.path.join(P.REPORTS_DIR, 'exception_summary.md')


def n(v):
    return '{:,}'.format(v or 0)


def main():
    from normalize import policy as policy_mod, document
    pol = policy_mod.load()
    stages_run = set(document.STAGES_RUN)

    occ = Counter()        # 규칙 → 발생 건수 합
    parts = Counter()      # 규칙 → 그 규칙이 걸린 part 수
    docs = Counter()       # 규칙 → 그 규칙이 걸린 문서 수
    samples = {}
    by_group = {}          # 규칙 → 문서군 → 발생 건수

    n_docs = n_parts = 0
    if not os.path.isdir(P.INTERIM_DOCS_DIR):
        print('doc.json 이 없다. scripts/03_build_docjson.py 를 먼저.')
        return 3

    files = sorted(f for f in os.listdir(P.INTERIM_DOCS_DIR)
                   if f.endswith('.json.gz'))
    for fn in files:
        with gzip.open(os.path.join(P.INTERIM_DOCS_DIR, fn), 'rt',
                       encoding='utf-8') as f:
            p = json.load(f)
        n_docs += 1
        g = p.get('doc_group')
        seen = set()
        for part in p.get('parts') or []:
            n_parts += 1
            for a in part.get('actions') or []:
                r = a['rule']
                occ[r] += a.get('count', 1)
                parts[r] += 1
                seen.add(r)
                by_group.setdefault(r, Counter())[g] += a.get('count', 1)
                if r not in samples and a.get('samples'):
                    samples[r] = a['samples'][:4]
        for r in seen:
            docs[r] += 1

    # parse_report — 문서 단위 결과
    statuses = Counter()
    if os.path.isfile(P.PARSE_REPORT):
        with open(P.PARSE_REPORT, encoding='utf-8') as f:
            for line in f:
                statuses[json.loads(line).get('status')] += 1

    L = []
    A = L.append
    A('# 예외 발생 집계')
    A('')
    A('`config/exception_policy.yaml` 에 적힌 정책과, `data/interim/docs/` '
      '%s개 문서(part %s개)에 실제로 남은 조치 기록을 대조한다.'
      % (n(n_docs), n(n_parts)))
    A('')
    A('정책 %d개 / version %s' % (len(pol), pol.version))
    A('')

    # ── 문서 처리 결과 ────────────────────────────────────────────
    A('## 문서 처리 결과')
    A('')
    A('| status | 문서 |')
    A('|---|---:|')
    for k in sorted(statuses, key=lambda k: -statuses[k]):
        A('| `%s` | %s |' % (k, n(statuses[k])))
    A('| **합계** | **%s** |' % n(sum(statuses.values())))
    A('')
    fail = sum(v for k, v in statuses.items()
               if k not in ('ok', 'no_source_xml'))
    if fail:
        A('> ⚠ 실패 %s건. `python scripts/03_build_docjson.py --only-failed`'
          % n(fail))
    else:
        A('실패 0건. `no_source_xml` %s건은 XML 원문이 없는 pdf+html 문서로, '
          '**조용히 빠지지 않고 기록된 결손**이다.'
          % n(statuses.get('no_source_xml', 0)))
    A('')

    # ── 규칙별 ────────────────────────────────────────────────────
    A('## 규칙별 발생 건수')
    A('')
    A('| id | stage | handle | severity | 발생 | part | 문서 | 상태 |')
    A('|---|---|---|---|---:|---:|---:|---|')
    silent = []
    for r in pol.rules:
        c = occ.get(r.id, 0)
        htype = r.handle.get('type') or '—'
        wired = r.stage in stages_run
        if c:
            state = '기록됨'
        elif wired and r._rx is not None:
            # 돌긴 도는데 한 건도 안 걸렸다. 이건 진짜 의심스럽다.
            state = '⚠ **도는데 0건**'
            silent.append(r)
        elif r._rx is not None:
            # 정책에 있고 규칙도 유효한데 이 stage 를 아무도 안 부른다.
            state = '미연결 (`%s` stage 미가동)' % r.stage
        elif htype == 'defer':
            state = '5단계로 미룸'
        else:
            state = '코드가 판정 (엔진 밖)'
        A('| `%s` | %s | %s | %s | %s | %s | %s | %s |'
          % (r.id, r.stage or '—', htype, r.severity,
             n(c) if c else '—', n(parts.get(r.id, 0)) if c else '—',
             n(docs.get(r.id, 0)) if c else '—', state))
    A('')

    A('상태 읽는 법 — 이 셋은 서로 다른 사실이다.')
    A('')
    A('- **기록됨** — 규칙이 돌았고 걸렸다.')
    A('- **⚠ 도는데 0건** — 규칙이 돌았는데 한 건도 안 걸렸다. '
      '이 코퍼스에 정말 없거나, 규칙이 틀렸거나. 확인이 필요하다.')
    A('- **미연결** — 정책에 있고 규칙도 유효한데 그 stage 를 '
      '파이프라인이 아직 안 부른다. 0건이 아니라 **안 재봤다**는 뜻이다.')
    A('- **코드가 판정 (엔진 밖)** — 정책 엔진이 regex 로 셀 수 없는 규칙. '
      'E3 는 인코딩 층이, E4 는 순회가 판정하고 정책은 **무엇을 어떻게 '
      '기록할지**를 정한다. 실측치는 각 정책의 `measured:` 에 있다.')
    A('')
    A('지금 파이프라인이 돌리는 stage: %s'
      % ', '.join('`%s`' % s for s in sorted(stages_run)))
    A('')
    if silent:
        A('> ⚠ 아래 규칙은 실제로 도는데 이 코퍼스에서 0건이다. '
          '정말 없는 것인지 규칙이 틀린 것인지 확인이 필요하다: %s'
          % ', '.join('`%s`' % r.id for r in silent))
        A('')

    # ── 문서군별 ──────────────────────────────────────────────────
    if by_group:
        groups = ['exchange', 'major', 'holding', 'periodic']
        A('## 문서군별 발생 건수')
        A('')
        A('| id | ' + ' | '.join(groups) + ' | 합계 |')
        A('|---|' + '---:|' * (len(groups) + 1))
        for r in pol.rules:
            if r.id not in by_group:
                continue
            row = by_group[r.id]
            A('| `%s` | %s | %s |'
              % (r.id, ' | '.join(n(row.get(g, 0)) for g in groups),
                 n(sum(row.values()))))
        A('')

    # ── 표본 ──────────────────────────────────────────────────────
    if samples:
        A('## 실제 원문 표본')
        A('')
        for rid, ss in samples.items():
            A('**`%s`**' % rid)
            A('')
            for s in ss:
                A('- `%s`' % s.replace('`', '\\`').replace('|', '\\|'))
            A('')

    # ── 정책과 실측 대조 ──────────────────────────────────────────
    A('## 정책에 적힌 실측치 대조')
    A('')
    A('정책 파일의 `measured:` 는 1단계 census(`scripts/01_exception_census.py`)가 '
      '**별도 코드로** 잰 값이다. 아래 "이번 집계"는 doc.json 에 남은 조치 기록에서 '
      '나온다. 두 경로가 같은 답을 내야 한다.')
    A('')
    A('| id | 정책 measured | 이번 집계 | 일치 |')
    A('|---|---:|---:|---|')
    for r in pol.rules:
        m = r.measured.get('occurrences')
        if m is None:
            continue
        got = occ.get(r.id, 0)
        A('| `%s` | %s | %s | %s |'
          % (r.id, n(m), n(got), '✅' if m == got else '❌ **불일치**'))
    A('')

    A('<sub>생성: `scripts/04_exception_summary.py`</sub>')

    P.ensure_dirs(P.REPORTS_DIR)
    with open(OUT, 'w', encoding='utf-8') as w:
        w.write('\n'.join(L) + '\n')

    print('문서 %s개 / part %s개' % (n(n_docs), n(n_parts)))
    for r in pol.rules:
        if occ.get(r.id):
            print('  %-28s %10s건 / part %5s / 문서 %5s'
                  % (r.id, n(occ[r.id]), n(parts[r.id]), n(docs[r.id])))
    mism = [r.id for r in pol.rules
            if r.measured.get('occurrences') is not None
            and r.measured['occurrences'] != occ.get(r.id, 0)]
    print('')
    if mism:
        print('⚠ 정책 measured 와 불일치: %s' % ', '.join(mism))
    else:
        print('정책 measured 와 이번 집계가 전부 일치한다.')
    print('리포트: %s' % OUT)
    return 2 if mism else 0


if __name__ == '__main__':
    sys.exit(main())

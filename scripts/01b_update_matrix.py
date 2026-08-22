# -*- coding: utf-8 -*-
"""1단계 — 실측 결과를 docs/exception_matrix.md 에 주입한다.

`reports/exception_census.jsonl`(01_exception_census.py 산출)을 읽어
`<!-- CENSUS:BEGIN -->` … `<!-- CENSUS:END -->` 사이를 갈아끼운다.

숫자를 손으로 옮겨 적지 않는다. 문서의 숫자와 측정값이 어긋날 방법을
없애는 게 목적이다. 다시 재면 다시 주입하면 된다.

    python scripts/01b_update_matrix.py
"""
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

CENSUS = os.path.join(P.REPORTS_DIR, 'exception_census.jsonl')
MATRIX = os.path.join(P.DOCS_DIR, 'exception_matrix.md')
BEGIN = '<!-- CENSUS:BEGIN -->'
END = '<!-- CENSUS:END -->'


def n(v):
    return '{:,}'.format(v or 0)


def load():
    rows = []
    with open(CENSUS, encoding='utf-8') as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def build_block(recs):
    ok = [r for r in recs if r.get('status') == 'ok']
    bad = [r for r in recs if r.get('status') != 'ok']
    groups = ['exchange', 'major', 'holding', 'periodic']
    groups = [g for g in groups if any(r['doc_group'] == g for r in ok)]

    def tot(rs, k):
        return sum(r.get(k) or 0 for r in rs)

    def ndocs(rs, k, pred=lambda v: v and v > 0):
        return sum(1 for r in rs if pred(r.get(k)))

    L = []
    A = L.append
    A('원문 **%s개**를 읽어 다시 잰 값이다 (명세의 숫자를 옮겨 적지 않았다).'
      % n(len(ok)))
    if bad:
        A('')
        A('> 스캔 실패 %d건: %s'
          % (len(bad), ', '.join(sorted(set(r['status'] for r in bad)))))
    A('')

    # ── 문서군별 원표 ──────────────────────────────────────────────
    A('### 문서군별')
    A('')
    A('| 문서군 | 문서 | E1 bare `&` | E2 bare `<` | E3 한글<5% | E4 `//SECTION-2` 정규식 → 도달 | LIBRARY 노드 | E5 TABLE | E5 표 아님 | E6 단위캡션 | E7 기수표기 | E8 TE(ACODE) |')
    A('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    NA = '—'          # 0건이 아니라 '잴 것이 없음'
    for g in groups + ['**합계**']:
        rs = ok if g.startswith('**') else [r for r in ok if r['doc_group'] == g]
        # DSD 트리를 안 훑는 문서군(exchange=HTML)은 E4~E8이 측정 대상이 아니다.
        dsd = any(r.get('tree_scan') == 'dsd_xml' for r in rs)

        def d(v):
            return v if dsd else NA

        s2r, s2d = tot(rs, 'e4_section2_regex'), tot(rs, 'e4_section2_reached')
        arrow = '%s → %s' % (n(s2r), n(s2d))
        if s2r != s2d:
            arrow += ' ⚠**%s 유실**' % n(s2r - s2d)
        elif s2r:
            arrow += ' (유실 0)'
        deg = tot(rs, 'e5_degenerate')
        tab = tot(rs, 'e5_tables')
        degcell = ('%s (%.0f%%)' % (n(deg), 100.0 * deg / tab)) if tab else NA
        A('| %s | %s | %s건 / %s문서 | %s건 / %s문서 | %s | %s | %s | %s | %s | %s | %s | %s |'
          % (g, n(len(rs)),
             n(tot(rs, 'e1_bare_amp')), n(ndocs(rs, 'e1_bare_amp')),
             n(tot(rs, 'e2_bare_lt')), n(ndocs(rs, 'e2_bare_lt')),
             n(ndocs(rs, 'e3_ratio', lambda v: v is not None and v < 0.05)),
             d(arrow), d(n(tot(rs, 'e4_library_nodes'))),
             d(n(tab)), degcell if dsd else NA,
             d(n(tot(rs, 'e6_unit_captions'))),
             n(tot(rs, 'e7_period_labels')),
             d(n(tot(rs, 'e8_te_with_acode')))))
    A('')
    A('exchange는 HTML이라 SECTION·TE·ACODE 자체가 없다. E4~E8은 0건이 아니라 '
      '**측정 대상 아님**이라 `—`로 둔다 — 0건과 "잴 것이 없음"은 다른 사실이다. '
      'E1~E3·E7은 원문 텍스트 기준이라 exchange에도 그대로 적용된다.')
    A('')

    # ── 항목별 결론 ────────────────────────────────────────────────
    A('### 항목별 결론')
    A('')

    # E3
    lo = [r for r in ok if (r.get('e3_ratio') or 0) < 0.05]
    worst = sorted(ok, key=lambda r: r.get('e3_ratio') or 0)[:3]
    A('**E3** — 5%% 미만 문서 **%d건**. 최저 %s.' % (
        len(lo), ', '.join('`%s` %.1f%%' % (r['doc_id'], 100 * (r['e3_ratio'] or 0))
                           for r in worst)))
    A('')
    A('> 탐지법을 한 번 고쳤다. 태그를 정규식으로 지운 문자열을 분모로 쓰면 '
      'DSD-XML의 긴 속성이 분모를 부풀려, 한글 음절이 198,955개나 되는 멀쩡한 '
      '13.7MB 문서(`periodic_20251114002900`)가 2.84%%로 나와 "한글 전량 파괴"로 '
      '잡혔다. E3의 존재 이유가 그 파괴를 잡는 것인데 멀쩡한 문서에서 울리면 '
      '검사가 아니라 소음이다. → **파서가 실제로 읽어낸 본문**을 분모로 쓴다.')
    A('')

    # E4
    s2r, s2d = tot(ok, 'e4_section2_regex'), tot(ok, 'e4_section2_reached')
    A('**E4** — `//SECTION-2` 정규식 %s개 / 순회 도달 %s개 / **유실 %s개**. '
      'LIBRARY 컨테이너는 %s개로 실재한다.'
      % (n(s2r), n(s2d), n(s2r - s2d), n(tot(ok, 'e4_library_nodes'))))
    A('')
    A('기존 `_walk`의 catch-all 재귀가 컨테이너를 그냥 통과하기 때문에 '
      '유실이 0이다. 명세가 경고한 30%% 유실은 **직속 자식 경로를 쓸 때** 생기며, '
      '기존 코드는 그 경로를 쓰지 않는다. 다만 이건 의도가 아니라 부수효과라서, '
      '`structure` 검증으로 못 박아 두지 않으면 언제든 조용히 깨진다.')
    A('')

    # E5
    tab, deg = tot(ok, 'e5_tables'), tot(ok, 'e5_degenerate')
    A('**E5** — TABLE %s개 중 **%s개(%.1f%%)가 `rows<=1 or cols<=1`**.'
      % (n(tab), n(deg), 100.0 * deg / max(1, tab)))
    A('')

    # E6
    uc = tot(ok, 'e6_unit_captions')
    fc = tot(ok, 'e6_footnote_captions')

    def p(k, base):
        return 100.0 * tot(ok, k) / max(1, base)

    A('**E6** — 귀속 방향을 "바로 옆 형제가 진짜 표인가"로 쟀다.')
    A('')
    A('| 캡션 | 건수 | 뒤에만 | 앞에만 | 양쪽 | 없음 |')
    A('|---|---:|---:|---:|---:|---:|')
    A('| `(단위 : …)` | %s | **%.1f%%** | %.1f%% | %.1f%% | %.1f%% |'
      % (n(uc), p('e6_unit_next_only', uc), p('e6_unit_prev_only', uc),
         p('e6_unit_both', uc), p('e6_unit_neither', uc)))
    A('| `※ …` | %s | %.1f%% | **%.1f%%** | %.1f%% | %.1f%% |'
      % (n(fc), p('e6_foot_next_only', fc), p('e6_foot_prev_only', fc),
         p('e6_foot_both', fc), p('e6_foot_neither', fc)))
    A('')
    A('단위표는 **다음 형제에 귀속**이 확정적이다 — 뒤쪽에만 붙은 것이 %.1f%%인데 '
      '앞쪽에만 붙은 것은 %.1f%%뿐이다. 명세대로다.'
      % (p('e6_unit_next_only', uc), p('e6_unit_prev_only', uc)))
    A('')
    A('각주는 명세대로 **이전 형제** 쪽으로 기울지만(앞에만 %.1f%% vs 뒤에만 %.1f%%), '
      '**%.1f%%는 양옆 어디에도 진짜 표가 없다.** 즉 각주 캡션의 상당수는 표의 '
      '각주가 아니라 그냥 떠 있는 주석이다. "각주표는 이전 형제에 귀속"을 무조건 '
      '적용하면 없는 귀속을 만들어낸다 — 옆에 진짜 표가 있을 때만 붙여야 한다.'
      % (p('e6_foot_prev_only', fc), p('e6_foot_next_only', fc),
         p('e6_foot_neither', fc)))
    A('')

    # E7
    A('**E7** — 기수 표기(`제 N 기`) %s건. 이걸 실제 날짜에 매핑하는 코드는 '
      '기존에 0곳이다.' % n(tot(ok, 'e7_period_labels')))
    A('')

    # E8 — 핵심
    te = tot(ok, 'e8_te_with_acode')
    ac = tot(ok, 'e8_distinct_acode')
    tri = tot(ok, 'e8_distinct_triple')
    quad = tot(ok, 'e8_distinct_quad')
    A('**E8** — 키 후보별 유실률. ACODE를 가진 TE **%s개** 기준.' % n(te))
    A('')
    A('| 키 | 살아남는 레코드 | 유실률 |')
    A('|---|---:|---:|')
    A('| `{acode: value}` (명세가 경고한 것) | %s | **%.1f%%** |'
      % (n(ac), 100.0 * (te - ac) / max(1, te)))
    A('| `(table_idx, row_idx, acode)` (명세가 **요구한** 것) | %s | **%.2f%%** |'
      % (n(tri), 100.0 * (te - tri) / max(1, te)))
    A('| `(table_idx, row_idx, col_idx, acode)` | %s | **%.2f%%** |'
      % (n(quad), 100.0 * (te - quad) / max(1, te)))
    A('')
    A('**명세가 지정한 키도 안전하지 않다.** `(table_idx, row_idx, acode)`는 '
      '%.2f%%를 잃는다. 원인은 **같은 행 안에서 같은 ACODE가 여러 열에 반복**되는 '
      '구조(당기/전기, 지배/비지배 같은 다열 표)이고, 실측 %s건이다. '
      '열 번호를 키에 넣으면 유실이 %.2f%%로 떨어진다.'
      % (100.0 * (te - tri) / max(1, te), n(tot(ok, 'e8_same_row_repeat')),
         100.0 * (te - quad) / max(1, te)))
    A('')
    A('→ 5단계 `extract/acode.py`의 키는 명세의 3튜플이 아니라 '
      '**`(table_idx, row_idx, col_idx, acode)`** 로 간다. '
      '명세의 "dict 금지"라는 의도는 그대로 지키되, 키 자체를 한 칸 넓힌다.')
    A('')

    # E2 표본
    from collections import Counter
    c = Counter()
    for r in ok:
        for smp in (r.get('e2_samples') or []):
            c[smp] += 1
    if c:
        A('### E2 bare `<` 실제 표본')
        A('')
        A('| 문서 수 | 원문 조각 |')
        A('|---:|---|')
        for smp, k in c.most_common(12):
            A('| %d | `%s` |' % (k, smp.replace('|', '\\|')))
        A('')

    A('<sub>생성: `scripts/01_exception_census.py` → `scripts/01b_update_matrix.py`. '
      '원자료 `reports/exception_census.jsonl`.</sub>')
    return '\n'.join(L)


def main():
    if not os.path.isfile(CENSUS):
        print('실측 파일이 없다: %s' % CENSUS)
        print('먼저 python scripts/01_exception_census.py 를 돌려라.')
        return 3
    recs = load()
    block = build_block(recs)

    with open(MATRIX, encoding='utf-8') as f:
        doc = f.read()
    if BEGIN not in doc or END not in doc:
        print('%s 에 CENSUS 마커가 없다.' % MATRIX)
        return 2
    head = doc[:doc.index(BEGIN) + len(BEGIN)]
    tail = doc[doc.index(END):]
    with open(MATRIX, 'w', encoding='utf-8') as w:
        w.write(head + '\n' + block + '\n' + tail)
    print('주입 완료: %s (%d줄)' % (MATRIX, block.count('\n') + 1))
    return 0


if __name__ == '__main__':
    sys.exit(main())

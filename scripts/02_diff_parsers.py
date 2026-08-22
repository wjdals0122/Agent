# -*- coding: utf-8 -*-
"""2단계 준비 — 네 파서의 최상위 정의를 함수 단위로 대조한다.

관심사 분리를 하려면 "무엇을 공통으로 뽑아도 안전한가"를 먼저 알아야 한다.
major/holding/periodic 은 같은 골격을 쓰지만 **검증된 차이**가 있고
(PARSING_NOTES: 배경 음영 머리글, 행 단위 colspan 등), 그 차이를 모르고
합치면 4,204건에서 검증된 자산이 조용히 깨진다. 절대 규칙 6.

그래서 소스를 직접 비교한다.
  · 완전 동일        → src/normalize/ 로 뽑아도 안전
  · 다름            → 뽑지 않는다. 무엇이 다른지 출력한다.
  · 한쪽에만 있음    → 그 파서 고유

    python scripts/02_diff_parsers.py
    python scripts/02_diff_parsers.py --show _live_columns
"""
import argparse
import ast
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PARSERS = ['major_parser', 'holding_parser', 'periodic_parser',
           'exchange_parser']


def top_level_defs(path):
    """파일에서 최상위 def/class 의 이름 → 소스 조각."""
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    lines = src.splitlines()
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            seg = '\n'.join(lines[node.lineno - 1:node.end_lineno])
            out[node.name] = seg
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out[t.id] = '\n'.join(
                        lines[node.lineno - 1:node.end_lineno])
    return out


def methods_of(path, classname):
    """클래스 하나의 메서드 이름 → 소스 조각."""
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    lines = src.splitlines()
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == classname:
            for m in node.body:
                if isinstance(m, ast.FunctionDef):
                    out[m.name] = '\n'.join(
                        lines[m.lineno - 1:m.end_lineno])
    return out


def norm(s):
    """비교용 정규화 — 들여쓰기/빈 줄만 맞추고 내용은 안 건드린다."""
    return '\n'.join(l.rstrip() for l in s.strip().splitlines() if l.strip())


def compare(name, tables, keys):
    """tables: {파서: {이름: 소스}}. keys: 비교할 파서 목록."""
    allnames = set()
    for k in keys:
        allnames |= set(tables[k])

    same, diff, only = [], [], []
    for nm in sorted(allnames):
        have = [k for k in keys if nm in tables[k]]
        if len(have) == 1:
            only.append((nm, have[0]))
            continue
        srcs = {k: norm(tables[k][nm]) for k in have}
        first = srcs[have[0]]
        if all(srcs[k] == first for k in have):
            same.append((nm, have, len(tables[have[0]][nm].splitlines())))
        else:
            groups = {}
            for k in have:
                groups.setdefault(srcs[k], []).append(k)
            diff.append((nm, have, list(groups.values())))
    return same, diff, only


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', help='이 이름의 실제 diff 를 출력한다')
    a = ap.parse_args(argv)

    top = {}
    for m in PARSERS:
        top[m] = top_level_defs(os.path.join(P.PARSER_DIR, m + '.py'))

    dsd = ['major_parser', 'holding_parser', 'periodic_parser']

    if a.show:
        nm = a.show
        have = [m for m in PARSERS if nm in top[m]]
        if not have:
            # 클래스 메서드일 수도 있다
            for m in PARSERS:
                cls = 'MajorParser' if m == 'major_parser' else (
                    'HoldingParser' if m == 'holding_parser' else (
                        'PeriodicParser' if m == 'periodic_parser'
                        else 'ExchangeParser'))
                ms = methods_of(os.path.join(P.PARSER_DIR, m + '.py'), cls)
                if nm in ms:
                    top[m][nm] = ms[nm]
                    have.append(m)
        if len(have) < 2:
            print('%s: 비교할 대상이 부족하다 (%s)' % (nm, have))
            return 1
        base = have[0]
        for other in have[1:]:
            print('═' * 70)
            print('%s  vs  %s   —   %s' % (base, other, nm))
            print('═' * 70)
            for line in difflib.unified_diff(
                    norm(top[base][nm]).splitlines(),
                    norm(top[other][nm]).splitlines(),
                    base, other, lineterm='', n=2):
                print(line)
        return 0

    print('═' * 74)
    print('DSD 세 파서(major / holding / periodic) 최상위 정의 대조')
    print('═' * 74)
    same, diff, only = compare('top', top, dsd)

    print('')
    print('■ 완전 동일 — src/normalize/ 로 뽑아도 안전 (%d개)' % len(same))
    tot = 0
    for nm, have, nlines in same:
        mark = '' if len(have) == 3 else '  [%s 만]' % '+'.join(
            h.split('_')[0] for h in have)
        print('    %-22s %4d줄%s' % (nm, nlines, mark))
        if len(have) == 3:
            tot += nlines
    print('    → 3파서 공통 %d줄' % tot)

    print('')
    print('■ 이름은 같은데 내용이 다름 — 뽑지 않는다 (%d개)' % len(diff))
    for nm, have, groups in diff:
        gs = ' | '.join('+'.join(g.split('_')[0] for g in grp)
                        for grp in groups)
        print('    %-22s %s' % (nm, gs))
    if diff:
        print('    → --show 이름  으로 실제 차이를 본다')

    print('')
    print('■ 한 파서에만 있음 (%d개)' % len(only))
    for nm, who in only:
        print('    %-22s %s' % (nm, who.split('_')[0]))

    # 파서 클래스 메서드도 같은 방식으로
    print('')
    print('═' * 74)
    print('파서 클래스 메서드 대조')
    print('═' * 74)
    cls_of = {'major_parser': 'MajorParser',
              'holding_parser': 'HoldingParser',
              'periodic_parser': 'PeriodicParser'}
    mt = {}
    for m in dsd:
        mt[m] = methods_of(os.path.join(P.PARSER_DIR, m + '.py'), cls_of[m])
    same2, diff2, only2 = compare('cls', mt, dsd)
    print('')
    print('■ 완전 동일 (%d개)' % len(same2))
    for nm, have, nlines in same2:
        mark = '' if len(have) == 3 else '  [%s 만]' % '+'.join(
            h.split('_')[0] for h in have)
        print('    %-22s %4d줄%s' % (nm, nlines, mark))
    print('')
    print('■ 내용이 다름 (%d개)' % len(diff2))
    for nm, have, groups in diff2:
        gs = ' | '.join('+'.join(g.split('_')[0] for g in grp)
                        for grp in groups)
        print('    %-22s %s' % (nm, gs))
    print('')
    print('■ 한 파서에만 있음 (%d개)' % len(only2))
    for nm, who in only2:
        print('    %-22s %s' % (nm, who.split('_')[0]))
    return 0


if __name__ == '__main__':
    sys.exit(main())

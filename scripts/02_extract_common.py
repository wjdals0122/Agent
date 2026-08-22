# -*- coding: utf-8 -*-
"""2단계 — 공통 층 추출을 parser/*.py 에 반영한다.

`scripts/02_diff_parsers.py` 가 **완전 동일**하다고 확인한 정의만 지우고,
`src/normalize/` 에서 같은 이름으로 import 한다. 손으로 줄을 지우지 않고
AST 의 줄 범위로 지운다 — 줄 번호가 밀려서 엉뚱한 걸 지우는 사고를
막으려는 것이다.

동작은 바뀌지 않아야 한다. 확인은 `scripts/99_validate.py --baseline`.

    python scripts/02_extract_common.py --dry-run
    python scripts/02_extract_common.py
    python scripts/02_extract_common.py --restore     # 백업에서 되돌린다
"""
import argparse
import ast
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 세 DSD 파서에서 완전 동일했던 최상위 정의 → src/normalize/ 로 이사
REMOVE = [
    # value.py
    '_RE_MULTISPACE', '_RE_INVISIBLE', '_EMPTY_VALUES',
    '_RE_ISO8', '_RE_ISO_RANGE', '_RE_COLON_LABEL',
    'clean', 'flat', 'is_empty_value', '_int', '_esc',
    # tree.py
    '_Node', '_walk', '_text', '_own_nodes', '_own_tables', '_find',
    'CELL_TAGS', '_SECTION_TAGS', '_IGNORE',
    # encoding.py
    'decode',
]

IMPORT_BLOCK = '''
# ── 2단계: 공통 층 (src/normalize/) ─────────────────────────────────
# 아래 이름들은 major/holding/periodic 세 파서에서 글자 한 자 다르지 않아
# src/normalize/ 로 옮겼다(scripts/02_diff_parsers.py 로 확인). 이름과
# 동작은 그대로다 — 여기서 import 만 한다.
import _srcpath  # noqa: F401  (src/ 를 sys.path 에 얹는다)

from normalize.value import (                       # noqa: F401
    RE_MULTISPACE as _RE_MULTISPACE,
    RE_INVISIBLE as _RE_INVISIBLE,
    EMPTY_VALUES as _EMPTY_VALUES,
    RE_ISO8 as _RE_ISO8,
    RE_ISO_RANGE as _RE_ISO_RANGE,
    RE_COLON_LABEL as _RE_COLON_LABEL,
    clean, flat, is_empty_value,
    to_int as _int,
    escape_cell as _esc,
)
from normalize.tree import (                        # noqa: F401
    Node as _Node,
    CELL_TAGS,
    SECTION_TAGS as _SECTION_TAGS,
    IGNORE as _IGNORE,
    walk as _walk,
    text as _text,
    own_nodes as _own_nodes,
    own_tables as _own_tables,
    find as _find,
    in_thead as _tree_in_thead,
)
from normalize.encoding import decode_text as decode  # noqa: F401
from normalize.grid import expand as _grid_expand      # noqa: F401
# ────────────────────────────────────────────────────────────────────
'''

# 클래스 메서드 → 공통 함수로 위임 (self 를 안 쓰던 것들)
METHOD_DELEGATES = {
    '_expand': '''    def _expand(self, raw):
        """rowspan / colspan 을 펼쳐 빈틈 없는 2차원 표로 만든다.
        (본체는 normalize/grid.py — 세 파서에서 완전 동일했다.)"""
        return _grid_expand(raw, _Cell)
''',
    '_in_thead': '''    def _in_thead(self, tr):
        """(본체는 normalize/tree.py — 세 파서에서 완전 동일했다.)"""
        return _tree_in_thead(tr)
''',
    '_tag_text': '''    def _tag_text(self, root, tag):
        n = _find(root, tag)
        return flat(_text(n)) if n is not None else None
''',
}

TARGETS = ['major_parser', 'holding_parser', 'periodic_parser']
CLASS_OF = {'major_parser': 'MajorParser',
            'holding_parser': 'HoldingParser',
            'periodic_parser': 'PeriodicParser'}


def spans_to_remove(src, names):
    """최상위 정의의 (시작줄, 끝줄) 1-index 목록. 데코레이터 포함."""
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        nm = None
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            nm = node.name
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    nm = t.id
        if nm in names:
            start = node.lineno
            for d in getattr(node, 'decorator_list', []):
                start = min(start, d.lineno)
            out.append((start, node.end_lineno, nm))
    return sorted(out)


def method_span(src, classname, method):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == classname:
            for m in node.body:
                if isinstance(m, ast.FunctionDef) and m.name == method:
                    return m.lineno, m.end_lineno
    return None


def transform(path, classname, dry=False):
    src = open(path, encoding='utf-8').read()
    lines = src.splitlines(keepends=True)
    removed = []

    edits = []   # (start, end, replacement_text)
    for start, end, nm in spans_to_remove(src, set(REMOVE)):
        edits.append((start, end, ''))
        removed.append(nm)

    for mname, newsrc in METHOD_DELEGATES.items():
        sp = method_span(src, classname, mname)
        if sp:
            edits.append((sp[0], sp[1], newsrc))
            removed.append(classname + '.' + mname)

    # 뒤에서부터 적용해야 줄 번호가 안 밀린다
    edits.sort(key=lambda e: e[0], reverse=True)
    for start, end, repl in edits:
        lines[start - 1:end] = [repl] if repl else []

    out = ''.join(lines)

    # import 블록은 마지막 import 문 다음에 넣는다
    tree = ast.parse(out)
    last_import = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import = max(last_import, node.end_lineno)
    ol = out.splitlines(keepends=True)
    ol.insert(last_import, IMPORT_BLOCK)
    out = ''.join(ol)

    ast.parse(out)          # 문법이 깨졌으면 여기서 죽는다
    if not dry:
        shutil.copy2(path, path + '.step1.bak')
        with open(path, 'w', encoding='utf-8') as w:
            w.write(out)
    return removed, len(src.splitlines()), len(out.splitlines())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args(argv)

    if a.restore:
        for m in TARGETS:
            p = os.path.join(P.PARSER_DIR, m + '.py')
            if os.path.isfile(p + '.step1.bak'):
                shutil.copy2(p + '.step1.bak', p)
                print('되돌림: %s' % m)
        return 0

    for m in TARGETS:
        p = os.path.join(P.PARSER_DIR, m + '.py')
        removed, before, after = transform(p, CLASS_OF[m], dry=a.dry_run)
        print('%-18s %d줄 → %d줄  (%+d)  제거 %d개'
              % (m, before, after, after - before, len(removed)))
        print('    %s' % ', '.join(removed))
    if a.dry_run:
        print('')
        print('--dry-run 이라 파일은 안 건드렸다.')
    else:
        print('')
        print('백업: parser/*.py.step1.bak  (--restore 로 되돌린다)')
        print('이제 scripts/99_validate.py --baseline 으로 확인해라.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

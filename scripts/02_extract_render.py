# -*- coding: utf-8 -*-
"""2단계 — render 층 추출.

DSD 세 파서의 `to_markdown` 은 **기본 제목 문자열 하나만** 다르다
(scripts/02_diff_parsers.py --show to_markdown 로 확인). 57줄짜리 함수
세 벌을 `src/render/markdown.py:render_dsd` 한 벌로 합치고, 각 파서는
자기 제목만 넘기는 얇은 위임으로 바꾼다.

`_iso` 도 세 파서에서 정규식 이름만 같고 본문이 동일하므로 같이 옮긴다.

동작은 바뀌지 않아야 한다. 확인은 scripts/99_validate.py --baseline.

    python scripts/02_extract_render.py --dry-run
    python scripts/02_extract_render.py
    python scripts/02_extract_render.py --restore
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

TITLES = {
    'major_parser': '주요사항보고서',
    'holding_parser': '대량보유상황보고서',
    'periodic_parser': '사업보고서',
}

NEW_ISO = '''def _iso(norm):
    """AUNITVALUE 를 사람이 읽을 날짜로. 4가지 형식뿐임을 확인했다.
    (본체는 render/markdown.py — 세 파서에서 동일했다.)"""
    return _iso_impl(norm, _RE_ISO8, _RE_ISO_RANGE)
'''

NEW_MD = '''def to_markdown(doc, with_header=True):
    """조각 목록 → 마크다운. 본체는 render/markdown.py:render_dsd.

    DSD 세 파서의 원본은 기본 제목 문자열 하나만 달랐다. 그것만 넘긴다.
    """
    return _render_dsd(doc, %r, _iso, _esc, with_header)
'''

IMPORT_LINE = '''
# ── 2단계: render 층 (src/render/) ──────────────────────────────────
from render.markdown import (                       # noqa: F401
    render_dsd as _render_dsd,
    iso_from_aunitvalue as _iso_impl,
)
# ────────────────────────────────────────────────────────────────────
'''


def span(src, name):
    t = ast.parse(src)
    for n in t.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n.lineno, n.end_lineno
    return None


def transform(path, title, dry=False):
    src = open(path, encoding='utf-8').read()
    lines = src.splitlines(keepends=True)
    edits = []
    for name, new in (('to_markdown', NEW_MD % title), ('_iso', NEW_ISO)):
        sp = span(src, name)
        if sp:
            edits.append((sp[0], sp[1], new))
    edits.sort(key=lambda e: e[0], reverse=True)
    for a, b, repl in edits:
        lines[a - 1:b] = [repl]
    out = ''.join(lines)

    t = ast.parse(out)
    last = 0
    for n in t.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            last = max(last, n.end_lineno)
    ol = out.splitlines(keepends=True)
    ol.insert(last, IMPORT_LINE)
    out = ''.join(ol)
    ast.parse(out)

    if not dry:
        shutil.copy2(path, path + '.step2a.bak')
        with open(path, 'w', encoding='utf-8') as w:
            w.write(out)
    return len(src.splitlines()), len(out.splitlines()), len(edits)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args(argv)

    if a.restore:
        for m in TITLES:
            p = os.path.join(P.PARSER_DIR, m + '.py')
            if os.path.isfile(p + '.step2a.bak'):
                shutil.copy2(p + '.step2a.bak', p)
                print('되돌림: %s' % m)
        return 0

    for m, title in TITLES.items():
        p = os.path.join(P.PARSER_DIR, m + '.py')
        before, after, n = transform(p, title, dry=a.dry_run)
        print('%-18s %d줄 → %d줄  (%+d)  위임 %d개  제목 %r'
              % (m, before, after, after - before, n, title))
    if a.dry_run:
        print('\n--dry-run 이라 파일은 안 건드렸다.')
    else:
        print('\n백업: parser/*.py.step2a.bak')
    return 0


if __name__ == '__main__':
    sys.exit(main())

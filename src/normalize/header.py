# -*- coding: utf-8 -*-
"""헤더 밴드 판정 — 표의 어디까지가 머리글인가.

`extract/financials.py` 안에 갇혀 있던 로직을 꺼냈다. 헤더 밴드 판정은
재무제표 전용이 아니다 — 판정이 틀리면 데이터 행이 머리글로 먹히거나
머리글이 데이터로 섞이고, 그 결과가 총계 불일치(검증 6번 `sums`)로
드러난다. 그래서 판정 근거를 항상 같이 돌려준다.

신호는 둘이고 **순서가 중요하다.**

  1. 셀의 `is_header` — `THEAD` 안이거나 `USERMARK` 에 배경 음영(`BC0X`)이
     붙은 `TD`. PARSING_NOTES 실측으로 이 신호가 periodic 에서
     **43,998개** 표의 진짜 머리글을 살렸다(holding 은 1개). 놓치면
     "구분1/구분2/…" 같은 가짜 머리글이 생기면서 "당기말 유동" 같은
     실제 항목명이 사라진다. 검증된 자산이므로 **먼저** 본다.

  2. 숫자 경계 — 1번 신호가 없는 표를 위한 보조. 숫자가 나오는 행부터
     본문으로 본다.

⚠ 표 전체가 데이터 행 없이 통째로 음영이면 머리글로 오판하면 안 된다
  (holding 파서가 이미 그렇게 처리한다). `detect_band` 는 그 경우
  `source='all_shaded'` 로 표시하고 밴드를 비운다 — 부르는 쪽이
  판단할 수 있게 한다.
"""
import re

__all__ = ['RE_NUMERIC', 'detect_band', 'merge_rows', 'row_texts']

RE_NUMERIC = re.compile(r'^-?[\d,]+(?:\.\d+)?$')

MAX_BAND = 3          # 머리글 밴드가 3행을 넘는 표는 실측상 없다


def row_texts(grid_row):
    """격자 한 행 → 글자 목록. 빈 칸은 ''."""
    return [(c.text if c is not None else '') for c in grid_row]


def merge_rows(a, b):
    """여러 줄 머리글을 한 줄로 합친다. 같은 말이 겹치면 안 붙인다."""
    if not a:
        return list(b)
    out = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else ''
        y = b[i] if i < len(b) else ''
        out.append((x + ' ' + y).strip() if y and y not in x else x)
    return out


def _is_numeric_row(cells):
    return any(RE_NUMERIC.match((x or '').replace(' ', ''))
               for x in cells[1:] if x)


def detect_band(grid, max_band=MAX_BAND):
    """(header, body_start, source).

    header     합쳐진 머리글 한 줄 (열 수만큼)
    body_start 본문이 시작하는 행 번호
    source     'marked' | 'numeric_boundary' | 'all_shaded' | None
    """
    if not grid:
        return [], 0, None

    def marked(r):
        cs = [c for c in grid[r] if c is not None]
        return bool(cs) and all(getattr(c, 'is_header', False) for c in cs)

    # 표 전체가 음영이면 머리글이 아니라 그냥 강조된 표다
    if all(marked(r) for r in range(len(grid))):
        return [], 0, 'all_shaded'

    header = []
    r = 0
    while r < len(grid) and marked(r) and r < max_band:
        header = merge_rows(header, row_texts(grid[r]))
        r += 1
    if header:
        return header, r, 'marked'

    # 음영 신호가 없으면 숫자가 나오기 전까지
    start = 0
    for r in range(min(max_band, len(grid))):
        cells = row_texts(grid[r])
        if _is_numeric_row(cells):
            break
        if any(cells):
            header = merge_rows(header, cells)
            start = r + 1
    return header, start, ('numeric_boundary' if header else None)


def is_ragged(rows):
    """행마다 열 수가 다르면 True. 검증 골든셋 5번(`grid`)이 쓴다.

    `normalize.grid.expand` 는 끝에서 모든 행을 maxc 로 채우므로 정상
    경로에서는 항상 False 여야 한다. True 면 격자를 만든 쪽이 틀린 것이다.
    """
    if not rows:
        return False
    return len(set(len(r) for r in rows)) > 1

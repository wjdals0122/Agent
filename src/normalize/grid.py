# -*- coding: utf-8 -*-
"""rowspan / colspan 을 실제 2차원 격자로 펼친다.

원래 `_expand` 메서드. major/holding/periodic 세 파서에서 **완전 동일**
(41줄, `scripts/02_diff_parsers.py` 확인). `self` 를 안 써서 함수로 뺐다.

셀 클래스(`_Cell`)는 major 와 holding+periodic 이 서로 다르므로 여기서
만들지 않고 `cell_factory` 로 받는다. 다른 것을 합치지 않기 위해서다.

실측 규모: rowspan 최대 23, 한 행에 rowspan 6개, rowspan+colspan 동시
3,507군데(major 기준, exchange의 61배).

검증 골든셋 5번(`grid`)이 여기 결과를 본다 — 표별 열 수가 단일값인가
(ragged 0). 어긋나면 rowspan 복제나 헤더밴드 판정이 틀린 것이다.
"""

__all__ = ['expand', 'is_ragged']


def expand(raw, cell_factory):
    """rowspan / colspan 을 펼쳐 빈틈 없는 2차원 표로 만든다.

    raw          : 행마다 셀 노드 리스트
    cell_factory : (node, row_index) -> 셀 객체.
                   셀 객체는 .colspan / .rowspan 을 갖는다.

    반환: (grid, maxc). grid 의 각 칸은 셀 객체 또는 None.
    같은 셀 객체가 여러 칸에 **그대로 공유**된다(복사가 아니다) —
    holding·periodic 의 행 단위 colspan 제거가 "바로 왼쪽 칸과 같은 셀
    객체인가"로 판정하므로 이 동일성이 동작의 일부다.
    """
    grid, pending, maxc = [], {}, 0
    for r, src in enumerate(raw):
        row, col, i = [], 0, 0

        def put(idx, cell):
            while len(row) <= idx:
                row.append(None)
            row[idx] = cell

        while True:
            waiting = pending.get(col)
            if waiting:
                cell, left = waiting
                for k in range(cell.colspan):
                    put(col + k, cell)
                key = col
                col += cell.colspan
                left -= 1
                if left <= 0:
                    del pending[key]
                else:
                    pending[key] = (cell, left)
                continue
            if i >= len(src):
                break
            cell = cell_factory(src[i], r)
            i += 1
            for k in range(cell.colspan):
                put(col + k, cell)
            if cell.rowspan > 1:
                pending[col] = (cell, cell.rowspan - 1)
            col += cell.colspan

        maxc = max(maxc, len(row))
        grid.append(row)
    for row in grid:
        while len(row) < maxc:
            row.append(None)
    return grid, maxc


def is_ragged(grid):
    """detect 층 — 열 수가 행마다 다르면 True. 부작용 없음.

    `expand` 가 끝에서 모든 행을 maxc 로 채우므로 정상 경로에서는 항상
    False 여야 한다. True 면 격자를 만든 쪽이 잘못된 것이다.
    """
    if not grid:
        return False
    widths = set(len(r) for r in grid)
    return len(widths) > 1

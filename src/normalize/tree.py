# -*- coding: utf-8 -*-
"""태그 트리 — 노드 구조와 순회.

`scripts/02_diff_parsers.py` 대조 결과 major/holding/periodic 세 파서에서
**완전 동일**했던 정의만 옮겼다. 내용은 손대지 않았다 (절대 규칙 6).

`_TreeBuilder` 는 여기 없다. major 와 holding+periodic 이 서로 다르기
때문이다(`--show _TreeBuilder`). 다른 것을 같은 것처럼 합치지 않는다.

────────────────────────────────────────────────────────────────────────
절대 규칙 5 — 직속 자식 순회 금지
────────────────────────────────────────────────────────────────────────
`walk()` 와 `find()` 는 **descendant 축**이다. 직속 자식만 보는 순회를
여기에 추가하지 마라. `LIBRARY` 컨테이너 때문이다.

실측(4,616건): `LIBRARY` 노드 29,339개. 그런데
`grep -c LIBRARY parser/*.py` → major 1, holding 0, periodic 0.
holding·periodic 은 `LIBRARY` 라는 문자열을 한 번도 안 쓰면서 28,334개의
LIBRARY 컨테이너를 정확히 통과한다 — 문서 순회의 마지막 catch-all 재귀
한 줄 덕분이다. 그 줄을 "알 수 없는 태그는 건너뛴다"로 바꾸면
`//SECTION-2` 52,756개 중 상당수가 조용히 사라지고, **바꾼 사람은 자기가
무엇을 껐는지 알 방법이 없다.** 지운 줄에는 LIBRARY라는 말이 없다.

검증 골든셋 4번(`structure`)이 이걸 지킨다: `//SECTION-2` 개수 =
순회 도달 개수.
"""

__all__ = ['Node', 'CELL_TAGS', 'SECTION_TAGS', 'IGNORE',
           'walk', 'text', 'own_nodes', 'own_tables', 'find',
           'in_thead', 'tag_text']

CELL_TAGS = ('TD', 'TE', 'TU', 'TH')

SECTION_TAGS = {'SECTION-1': 2, 'SECTION-2': 3, 'SECTION-3': 4,
                'SECTION-4': 5}

IGNORE = {'PGBRK', 'COLGROUP', 'COL', 'SUMMARY', 'EXTRACTION',
          'DOCUMENT-NAME', 'FORMULA-VERSION', 'COMPANY-NAME'}


class Node:
    """원래 이름 `_Node`. 세 파서에서 완전 동일했다."""
    __slots__ = ('tag', 'attrs', 'children', 'parent', 'raw')

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children, self.raw = [], []


def walk(node):
    """descendant 축 전체 순회. 직속 자식만 보는 판본을 만들지 마라."""
    for c in node.children:
        yield c
        for g in walk(c):
            yield g


def text(node):
    out = []
    for r in node.raw:
        out.append(text(r) if isinstance(r, Node) else r)
    return ''.join(out)


def own_nodes(node):
    """이 표에 직접 속한 노드만. 중첩 표 안으로는 안 들어간다."""
    for c in node.children:
        if c.tag == 'TABLE':
            continue
        yield c
        for g in own_nodes(c):
            yield g


def own_tables(node):
    """이 표가 직접 품고 있는 중첩 표들 (한 겹만)."""
    out = []

    def rec(n):
        for c in n.children:
            if c.tag == 'TABLE':
                out.append(c)
            else:
                rec(c)

    rec(node)
    return out


def find(node, tag):
    for d in walk(node):
        if d.tag == tag:
            return d
    return None


def in_thead(tr):
    """원래 `_in_thead` 메서드. self 를 안 써서 함수로 뺐다.

    THEAD 안이면 TD도 라벨이 아니라 데이터다(major 예외②, 4,404행).
    """
    n = tr.parent
    while n is not None:
        if n.tag == 'THEAD':
            return True
        if n.tag == 'TABLE':
            return False
        n = n.parent
    return False


def tag_text(root, tag, flat_fn):
    """원래 `_tag_text` 메서드. flat 은 값 층에서 받는다."""
    n = find(root, tag)
    return flat_fn(text(n)) if n is not None else None

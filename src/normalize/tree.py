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

__all__ = ['Node', 'CELL_TAGS', 'SECTION_TAGS', 'IGNORE', 'CONTAINER_TAGS',
           'walk', 'text', 'para_text', 'own_nodes', 'own_tables', 'find',
           'in_thead', 'tag_text']

CELL_TAGS = ('TD', 'TE', 'TU', 'TH')

# 순회가 **반드시 통과해야 하는** 컨테이너. 이 목록을 코드가 조건문으로
# 쓰지는 않는다 — walk() 는 태그를 알든 모르든 무조건 내려가기 때문이다.
# 그런데도 이름을 적어 두는 이유는 하나다: `grep -rn LIBRARY src/` 가
# **순회 코드에 걸리게** 하려고. E4 의 위험은 순회를 "아는 태그만 내려간다"로
# 바꾸는 리팩터링이고, 그때 지워지는 줄에는 LIBRARY 라는 말이 없어서
# 바꾼 사람이 자기가 무엇을 껐는지 알 수 없다(실측 컨테이너 29,339개).
# 검증: 99_validate.py --structure 의 전수 대조 + LIBRARY 성질검사.
CONTAINER_TAGS = ('LIBRARY', 'BODY', 'TABLE-GROUP', 'DOCUMENT')

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


# 소제목으로 보이는 조각. '나. 소수주주권' / '1. 개요' / '(1) 배경' / '※ 주의'
# major 는 <SPAN> 을 회사명·날짜 같은 인라인 조각에도 쓴다. 태그 경계마다
# 공백을 넣으면 '두산에너빌리티' → '두산에너빌 리티', '(주)셀트리온' →
# '(주) 셀트리온', '2024 년 07 월' 처럼 멀쩡한 글자를 쪼갠다.
# 실측: major 109문서에서 공백 285개가 삽입됐는데 확실한 개선은 36개뿐이었다.
# 그래서 **소제목 모양일 때만** 경계로 인정한다.
import re as _re
_SUBTITLE_MAX = 25   # 소제목은 짧다. 실측상 이보다 긴 것은 문단이다.
_RE_SUBTITLE = _re.compile(
    r'^\s*(?:[가-힣]\.|\d{1,2}\s*[.)]|\(\s*\d{1,2}\s*\)|[※*])\s')


def para_text(node, subtitle_only=False):
    """문단(<P>) 안의 글자를 모으되, **태그 경계**에서 공백이 전혀 없으면
    한 칸 끼운다. 원문이 굵게 표시한 소제목 <SPAN>을 바로 뒤 문장에 공백
    없이 붙여 쓰는 경우가 있다 — 예: <SPAN>나. 소수주주권</SPAN>회사는...
    그대로 이어 붙이면 "나. 소수주주권회사는"처럼 라벨이 문장에 녹아들어
    회사 이름처럼 오독된다.

    ⚠️ node.raw의 조각 경계가 전부 '진짜' 태그 경계는 아니다. html.parser는
    이스케이프 안 된 '&'를 만나면(예: "P&A인수하여") 그 앞뒤 글자를 별개
    조각으로 쪼갠다 — 실제 원문엔 공백이 없는데도. 그래서 두 조각
    **모두** 문자열이면(둘 다 태그에서 나온 게 아니면) 공백을 넣지 않는다.
    조각 중 하나라도 <SPAN> 등 태그에서 나왔을 때만 공백 경계로 본다.

    표 칸(_text_no_table)에는 적용하지 않는다 — "1,234"+"백만원"처럼
    붙어야 자연스러운 숫자·단위 조합이 흔해서다.
    """
    parts = [(text(r), True) if isinstance(r, Node) else (r, False)
             for r in node.raw]
    out = []
    prev_from_tag = False
    # 루프 변수를 text 로 두면 모듈 함수 text() 를 가린다.
    prev_seg = ''
    for seg, from_tag in parts:
        boundary = (prev_from_tag or from_tag)
        if subtitle_only:
            # 앞 조각이 태그에서 나왔고, 그 내용 **전체가 짧은 소제목**일 때만.
            # 시작만 보면 안 된다 — 공백은 조각의 *끝*에 들어가는데,
            # '가. 합병의 …(긴 문단)…두산에너빌' 처럼 소제목으로 시작하는
            # 긴 조각이 통과해 회사명 한가운데를 쪼갠다. 실측으로 확인했다.
            ps = prev_seg.strip()
            boundary = (prev_from_tag and len(ps) <= _SUBTITLE_MAX
                        and bool(_RE_SUBTITLE.match(ps)))
        if (out and seg and out[-1] and boundary
                and not out[-1][-1].isspace() and not seg[0].isspace()):
            out.append(' ')
        out.append(seg)
        prev_from_tag = from_tag
        prev_seg = seg
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

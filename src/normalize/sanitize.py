# -*- coding: utf-8 -*-
"""E1 / E2 — 이스케이프 안 된 `&` 와 `<`. detect / handle 분리.

기존 코드는 이 둘을 **처리는 하는데 기록을 안 한다.**

    E1  `_TreeBuilder.handle_entityref` 가 html5 엔티티 사전에 있는 이름일
        때만 실제 문자로 바꾸고, 아니면 `&이름` 을 원문 그대로 되돌린다.
        무조건 `&이름;` 으로 복원하면 없던 세미콜론이 생긴다
        (원본 `삼성E&A` → `삼성E&A;`, exchange 73건 실측).
    E2  `html.parser` 가 `<` 다음이 영문자일 때만 태그로 본다. 그래서
        `<별표3-3>`, `<이사ㆍ감사 전체의 보수현황>` 이 글자로 살아남는다.

둘 다 잘 돌아간다. 문제는 **몇 건을 어떻게 처리했는지가 산출물에 안
남는다**는 것이다 (절대 규칙 2). 실측 규모: E1 84,167건 / 1,709문서,
E2 41,907건 / 1,352문서.

이 파일의 `detect_*` 는 **원문을 건드리지 않는다.** 세기만 한다. 그래서
기존 파싱 경로에 끼워 넣어도 마크다운이 한 바이트도 안 바뀐다.

`escape_*` 는 4단계(`config/exception_policy.yaml`)에서 쓸 handle 층이다.
지금 파이프라인은 이걸 **호출하지 않는다** — 기존 관대 파서가 이미
제 방식으로 처리하고 있고, 그 위에 치환을 얹으면 이중 처리가 된다.
"""
import re

__all__ = ['RE_BARE_AMP', 'RE_BARE_LT', 'RE_ANGLE_CAPTION',
           'detect_bare_amp', 'detect_bare_lt', 'detect',
           'escape_bare_amp', 'escape_bare_lt']

# 이미 이스케이프된 것은 건드리지 않는다
RE_BARE_AMP = re.compile(
    r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)')

# `<` 다음이 태그 시작(/ ? ! 영문자)이 아니면 글자다
RE_BARE_LT = re.compile(r'<(?![/?!]|[A-Za-z])')

# `< TV 시장점유율 추이 >` 같은 표 캡션 통짜로 뽑기 (진단용)
RE_ANGLE_CAPTION = re.compile(r'<[^<>\n]{0,60}>')


def detect_bare_amp(text):
    """부작용 없음. 위치와 표본을 담은 진단 dict."""
    hits = [m.start() for m in RE_BARE_AMP.finditer(text)]
    samples = []
    for i in hits[:5]:
        samples.append(text[i:i + 12].replace('\n', ' '))
    return {'rule': 'E1_bare_ampersand', 'count': len(hits),
            'samples': samples}


def detect_bare_lt(text):
    """부작용 없음. 실제 꺾쇠 조각을 표본으로 준다."""
    hits = [m.start() for m in RE_BARE_LT.finditer(text)]
    samples = []
    for m in RE_ANGLE_CAPTION.finditer(text):
        if RE_BARE_LT.match(m.group(0)):
            samples.append(m.group(0))
            if len(samples) >= 5:
                break
    return {'rule': 'E2_bare_lt', 'count': len(hits), 'samples': samples}


def detect(text):
    """E1·E2 를 한 번에. 건수가 0이면 빈 리스트 — 조용한 성공은 안 남긴다."""
    out = []
    for d in (detect_bare_amp(text), detect_bare_lt(text)):
        if d['count']:
            out.append(d)
    return out


def escape_bare_amp(text):
    """(결과, 조치기록). `&` → `&amp;`.

    ⚠ 지금 파이프라인은 안 쓴다. 기존 관대 파서가 이미 처리한다.
    4단계에서 정책이 이 함수를 부르게 되면, 그 전에 `_TreeBuilder` 의
    `handle_entityref` 를 꺼야 한다 — 안 그러면 이중 처리다.

    검증 골든셋 2번(`sanitize`)이 이걸 본다: 치환 횟수 × 4 = 문자수 증가분
    (`&` 1자 → `&amp;` 5자).
    """
    n = 0

    def sub(m):
        nonlocal n
        n += 1
        return '&amp;'

    out = RE_BARE_AMP.sub(sub, text)
    actions = ([{'rule': 'E1_bare_ampersand', 'action': 'escape',
                 'to': '&amp;', 'count': n,
                 'chars_added': len(out) - len(text)}] if n else [])
    return out, actions


def escape_bare_lt(text):
    """(결과, 조치기록). `<` → `&lt;`. 위와 같은 주의사항."""
    n = 0

    def sub(m):
        nonlocal n
        n += 1
        return '&lt;'

    out = RE_BARE_LT.sub(sub, text)
    actions = ([{'rule': 'E2_bare_lt', 'action': 'escape', 'to': '&lt;',
                 'count': n, 'chars_added': len(out) - len(text)}] if n else [])
    return out, actions

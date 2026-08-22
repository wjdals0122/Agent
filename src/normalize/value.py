# -*- coding: utf-8 -*-
"""글자 값 정리 — 네 파서가 **글자 한 자도 다르지 않게** 공유하던 것.

`scripts/02_diff_parsers.py` 로 major/holding/periodic 세 파서를 대조해
완전 동일함을 확인한 정의만 여기로 옮겼다. 내용은 손대지 않았다
(절대 규칙 6: 4,204건에서 검증된 자산이다).

이 층은 **부작용이 없다.** 문자열을 받아 문자열을 준다.
"""
import re

__all__ = ['RE_MULTISPACE', 'RE_INVISIBLE', 'EMPTY_VALUES',
           'RE_ISO8', 'RE_ISO_RANGE', 'RE_COLON_LABEL',
           'clean', 'flat', 'is_empty_value', 'to_int', 'escape_cell']

RE_MULTISPACE = re.compile(r'[ \t\u00a0\u3000]{2,}')
RE_INVISIBLE = re.compile(r'[\u200b-\u200d\ufeff\x00-\x08\x0b\x0c\x0e-\x1f]')
EMPTY_VALUES = {'-', '\u2013', '\u2014', '', '.', '\u00b7'}

RE_ISO8 = re.compile(r'^(\d{4})(\d{2})(\d{2})$')
RE_ISO_RANGE = re.compile(r'^(\d{8})-(\d{8})$')

# 표지의 "회 사 명 :" 같은 콜론 라벨
RE_COLON_LABEL = re.compile(r'^(.{1,30}?)\s*[:：]\s*$')


def clean(s):
    """DSD-XML은 셀 안에 태그가 거의 없어서 공백 정리만 하면 된다."""
    s = s.replace('\u00a0', ' ').replace('\u3000', ' ')
    s = RE_INVISIBLE.sub('', s)
    lines = [RE_MULTISPACE.sub(' ', l).strip() for l in s.split('\n')]
    return '\n'.join(l for l in lines if l)


def flat(s):
    """한 줄로 눌러 담는다 (표 셀, 제목용)."""
    return RE_MULTISPACE.sub(' ', clean(s).replace('\n', ' ')).strip()


def is_empty_value(s):
    return s.strip() in EMPTY_VALUES


def to_int(v, d=0):
    """원래 이름 `_int`. rowspan/colspan 속성값을 숫자로.

    ⚠ 원본 그대로다 — `int(v)` 만 한다. 양수 검사도, `str().strip()` 도
    없다. 그래서 '0' → 0, '-2' → -2, True → 1 이 된다.
    '개선'하지 않는다. 4,204건에서 검증된 동작이다 (절대 규칙 6).
    """
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def escape_cell(s):
    """원래 이름 `_esc`. 마크다운 표 셀 안의 `|` 와 줄바꿈.

    ⚠ 끝의 `.strip()` 은 원본에 있다. 빼면 표 셀 앞뒤 공백이 살아나
    베이스라인이 깨진다.
    """
    return s.replace('|', '\\|').replace('\n', '<br>').strip()

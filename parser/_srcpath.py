# -*- coding: utf-8 -*-
"""parser/*.py 가 src/ 를 찾게 해 주는 한 줄짜리 경로 부트스트랩.

parser/ 는 원래 "같은 폴더의 부품 4개만 있으면 도는" 자족 구조였다
(parser/README.md). 2단계에서 공통 층을 src/normalize/ 로 옮기면서 그
자족성이 깨지므로, 경로를 찾는 책임을 이 파일 하나에 모은다.

    import _srcpath  # noqa
"""
import os
import sys

_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

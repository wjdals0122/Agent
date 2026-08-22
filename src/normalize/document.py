# -*- coding: utf-8 -*-
"""XML → doc.json. 파이프라인의 축.

    원문 bytes ──▶ decode ──▶ 파서 ──▶ doc dict ──▶ (gzip json)
                                          │
                                          └──▶ render/markdown.py

XML 파싱은 여기서 **한 번만** 일어난다. 팩트 테이블·청크·인덱스는 전부
doc.json 에서만 파생된다. 청킹 전략을 바꿀 때 2.4MB(최대 13.7MB) XML을
다시 파싱하지 않기 위함이다.

왜 src/ 가 parser/ 를 import 하는가
    문서군별로 갈라지는 로직(major 와 holding+periodic 의 차이)은 아직
    parser/ 에 있고, 2단계에서 확인했듯 그 차이는 **검증된 자산**이라
    함부로 합칠 수 없다. 그래서 document.py 는 "무엇이 다른지"를 모른 채
    문서군 → 파서 를 이어 주는 얇은 층으로 둔다. 여기서 분기 로직을
    다시 쓰지 않는다 (절대 규칙 6).

JSON 왕복 안전성 (실측)
    doc dict 을 json 으로 굴린 뒤 다시 렌더링해도 마크다운이 바이트
    동일하다. 네 문서군 24건 표본 검사 불일치 0건. chunk 가 전부
    str / None / list 이고, 렌더러가 색인과 언패킹만 쓰기 때문에
    tuple → list 변환을 구분하지 않는다.
"""
import os
import sys

__all__ = ['SUPPORTED', 'build_doc', 'doc_to_json', 'json_to_doc',
           'part_key_for']

SUPPORTED = ('exchange', 'major', 'holding', 'periodic')

SCHEMA = 'dart.doc/1'

_PARSER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'parser')

_MODS = {}


def _parsers():
    if _MODS:
        return _MODS
    if _PARSER_DIR not in sys.path:
        sys.path.insert(0, _PARSER_DIR)
    import exchange_parser
    import major_parser
    import holding_parser
    import periodic_parser
    _MODS.update({
        'exchange': (exchange_parser, 'ExchangeParser'),
        'major': (major_parser, 'MajorParser'),
        'holding': (holding_parser, 'HoldingParser'),
        'periodic': (periodic_parser, 'PeriodicParser'),
    })
    return _MODS


def build_doc(raw_bytes, doc_group, file_path=None, corp_name=None,
              receipt_no=None, drop_empty=True):
    """원문 bytes → (doc, actions).

    절대 규칙 4 — 파서에 bytes 를 넘기지 않는다. decode 는 여기서 한다.
    actions 는 인코딩·정제 과정에서 무슨 일이 있었는지의 기록이며,
    아무 일도 없었으면 빈 리스트다 (절대 규칙 2).
    """
    if doc_group not in SUPPORTED:
        raise ValueError('파서가 없는 문서군: %r' % doc_group)

    from normalize import encoding, sanitize

    mod, clsname = _parsers()[doc_group]
    source, actions = encoding.decode(raw_bytes)

    # E1/E2 는 **세기만** 한다. 원문을 바꾸지 않으므로 출력에 영향이 없다.
    # 기존 관대 파서가 이미 제 방식으로 처리하고 있어서, 여기서 치환하면
    # 이중 처리가 된다 (normalize/sanitize.py 머리말 참조).
    actions.extend(sanitize.detect(source))

    if corp_name is None and file_path is not None:
        corp_name = mod.corp_name_from_path(file_path)

    parser = getattr(mod, clsname)(drop_empty=drop_empty)
    doc = parser.parse(source, receipt_no, corp_name)
    return doc, actions


def render(doc, doc_group, with_header=False):
    """doc dict → 마크다운. 입력이 트리가 아니라 **doc** 이다.

    doc 은 방금 파싱한 것이든 doc.json 에서 읽은 것이든 상관없다.
    """
    mod, _ = _parsers()[doc_group]
    return mod.to_markdown(doc, with_header=with_header)


def part_key_for(doc_id, file_path):
    """md 파일 이름에 쓰이는 키. 접수번호가 겹치는 첨부는 꼬리를 붙인다.

    periodic 한 접수번호에 본보고서 + 감사보고서(_00760) +
    연결감사보고서(_00761) 최대 3개가 들어간다.
    """
    stem = os.path.splitext(os.path.basename(file_path))[0]
    rcept = doc_id.split('_', 1)[1] if '_' in doc_id else doc_id
    tail = stem[len(rcept):] if stem.startswith(rcept) else ''
    return doc_id + tail


def doc_to_json(doc_id, meta, parts):
    """doc.json 한 건. parts 는 원문 파일 하나당 한 항목."""
    return {
        'schema': SCHEMA,
        'doc_id': doc_id,
        'doc_group': meta.get('doc_group'),
        'corp_code': meta.get('corp_code'),
        'corp_name': meta.get('corp_name'),
        'rcept_no': meta.get('rcept_no'),
        'rcept_dt': meta.get('rcept_dt'),
        'report_nm': meta.get('report_nm'),
        'is_correction': meta.get('is_correction'),
        'n_parts': len(parts),
        'parts': parts,
    }


def json_to_doc(part):
    """doc.json 의 part 하나에서 렌더러가 먹을 doc dict 을 꺼낸다."""
    return part['doc']

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

# 이 파이프라인이 실제로 돌리는 정책 stage. 여기 없는 stage 의 규칙은
# 정책에 적혀 있어도 **아무도 부르지 않는다.**
# reports/exception_summary.md 가 "0건"과 "안 붙었음"을 구분하는 근거다.
# table / parse stage 는 5단계 구조화 경로가 붙을 자리다.
STAGES_RUN = ('sanitize',)

# 5단계 구조화 대상 문서군.
#   financials  {XBRL} 재무제표 — periodic 에만 있다
#   acode       ACODE 기반 수시공시 — major / holding
# 정기공시 주석표 509종 44,037개는 구조화하지 않는다. md 청크로 흘려보내고
# parse_confidence: low 를 붙인다 (명세).
STRUCTURED = {'periodic': ('financials',),
              'major': ('acode',),
              'holding': ('acode',),
              'exchange': ('exchange',)}

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
              receipt_no=None, drop_empty=True, policy=None):
    """원문 bytes → (doc, actions).

    절대 규칙 4 — 파서에 bytes 를 넘기지 않는다. decode 는 여기서 한다.
    actions 는 인코딩·정제 과정에서 무슨 일이 있었는지의 기록이며,
    아무 일도 없었으면 빈 리스트다 (절대 규칙 2).

    policy: normalize.policy.Policy. 안 주면 기본 정책 파일을 읽는다.
            어떤 예외를 어떻게 다룰지는 **전부** 거기서 온다.
    """
    if doc_group not in SUPPORTED:
        raise ValueError('파서가 없는 문서군: %r' % doc_group)

    from normalize import encoding, policy as policy_mod

    mod, clsname = _parsers()[doc_group]
    source, actions = encoding.decode(raw_bytes)

    # 정제 규칙은 코드가 아니라 config/exception_policy.yaml 이 정한다.
    # 여기에는 E1/E2 같은 이름이 하나도 안 적혀 있다 — 정책에 무엇이
    # 들어 있든 그대로 돈다. 지금 정책은 전부 count_only 라 source 가
    # 그대로 돌아오고, 기록만 쌓인다.
    pol = policy or policy_mod.load()
    source, acts = pol.run_stage('sanitize', source)
    actions.extend(acts)

    if corp_name is None and file_path is not None:
        corp_name = mod.corp_name_from_path(file_path)

    parser = getattr(mod, clsname)(drop_empty=drop_empty)
    doc = parser.parse(source, receipt_no, corp_name)

    # ── 5단계: 구조화 경로 ──────────────────────────────────────────
    # 여기서 트리를 **한 번 더 세우지 않는다** — 파서가 방금 쓴 것과 같은
    # 소스를 쓰되, 구조화가 필요한 문서군에서만 트리를 세운다.
    # 결과는 doc.json 안에 들어가므로 팩트 테이블은 XML 을 다시 안 본다.
    struct, sacts = _structure(mod, source, doc_group, drop_empty, doc=doc)

    # 정정공시는 문서군을 가리지 않는다 — 넷 다 정정이 있다.
    from extract import corrections as corr
    cx = corr.build(doc, doc_group)
    if cx:
        struct = struct or {}
        struct['corrections'] = cx
        sacts.append({'rule': 'S5_corrections', 'stage': 'extract',
                      'action': 'structure', 'count': cx['n_pairs'],
                      'changed': cx['n_changed'],
                      'severity': 'info' if cx['n_pairs'] else 'warn'})
    if struct:
        doc['structured'] = struct
    actions.extend(sacts)

    # ── 검증 진단값 ────────────────────────────────────────────────
    # 검증 골든셋(structure/encoding/grid)이 5.5GB 를 다시 파싱하지 않게
    # 여기서 재 둔다. doc.json 이 축이라는 원칙을 검증에도 적용한 것이다.
    # 코드가 바뀌면 멱등 키(소스 해시)가 무효화되어 자동으로 다시 잰다.
    doc['diagnostics'] = _diagnose(mod, source, doc_group, actions, doc)
    return doc, actions


_RE_HANGUL = None
_RE_SECTION2 = None


def _chunk_text(doc):
    """조각에 실린 글자를 전부 이어 붙인다 — 실제로 산출물에 가는 글자."""
    out = []
    for c in doc.get('chunks') or []:
        k = c[0]
        if k == 'h':
            out.append(c[2])
        elif k == 'p':
            out.append(c[1])
        elif k == 'kv':
            out.extend(list(c[1]))
            if c[2]:
                out.append(c[2])
        elif k == 't':
            out.extend(c[1])
            for r in c[2]:
                out.extend(r)
    return ' '.join(x for x in out if x)


def _diagnose(mod, source, doc_group, actions, doc=None):
    """검증용 진단값. 판정하지 않고 **세기만** 한다.

    exchange 는 HTML 이라 SECTION-2·LIBRARY 가 없고, 파서도 공용 트리를
    안 쓴다(자기 _Node 를 쓴다). 그래서 트리 기반 진단은 DSD 만 재고,
    한글 비율은 **조각에 실린 글자**로 잰다 — 산출물에 실제로 가는 글자다.
    """
    global _RE_HANGUL, _RE_SECTION2
    import re
    if _RE_HANGUL is None:
        _RE_HANGUL = re.compile(r'[가-힣]')
        # 태그 이름 뒤 경계는 [\s>/] 로 직접 쓴다.  를 쓰면 편집 과정에서
        # 백스페이스 문자로 바뀌어도 에러 없이 0건이 되어 조용히 통과한다.
        _RE_SECTION2 = re.compile(r'<\s*SECTION-2(?=[\s>/])', re.IGNORECASE)

    from normalize import tree, grid as grid_mod, header as header_mod

    d = {'source_chars': len(source)}

    if doc_group == 'exchange':
        body = _chunk_text(doc or {})
        chars = len(re.sub(r'\s', '', body))
        d['hangul'] = len(_RE_HANGUL.findall(body))
        d['text_chars'] = chars
        d['hangul_ratio'] = round(d['hangul'] / chars, 4) if chars else 0.0
        d['tree_scan'] = 'n/a_html'
        esc0 = [a for a in actions if a.get('action') == 'escape']
        d['escape_ops'] = len(esc0)
        d['escape_count'] = sum(a.get('count', 0) for a in esc0)
        d['escape_chars_added'] = sum(a.get('chars_added', 0) for a in esc0)
        return d

    b = mod._TreeBuilder()
    b.feed(source)
    root = b.root
    d['tree_scan'] = 'dsd_xml'

    # E3 / encoding — 한글 음절 비율.
    # 분모는 태그를 지운 문자열이 아니라 **파서가 실제로 읽어낸 본문**이다
    # (1단계에서 확인: 태그 제거 정규식은 DSD 의 긴 속성 때문에 분모를
    #  부풀려 멀쩡한 13.7MB 문서를 2.84%로 만든다).
    body = tree.text(root)
    chars = len(re.sub(r'\s', '', body))
    d['hangul'] = len(_RE_HANGUL.findall(body))
    d['text_chars'] = chars
    d['hangul_ratio'] = round(d['hangul'] / chars, 4) if chars else 0.0

    # E4 / structure — //SECTION-2 정규식 개수 vs 순회 도달 개수
    if doc_group != 'exchange':
        d['section2_regex'] = len(_RE_SECTION2.findall(source))
        reached = 0
        libs = 0
        for n in tree.walk(root):
            if n.tag == 'SECTION-2':
                reached += 1
            elif n.tag == 'LIBRARY':
                libs += 1
        d['section2_reached'] = reached
        d['library_nodes'] = libs

        # grid — 격자를 펼친 뒤 열 수가 행마다 같은가 (ragged 0)
        ragged = 0
        ntab = 0
        for t in tree.walk(root):
            if t.tag != 'TABLE':
                continue
            trs = [e for e in tree.own_nodes(t) if e.tag == 'TR']
            raw = [[c for c in tr.children if c.tag in tree.CELL_TAGS]
                   for tr in trs]
            raw = [r for r in raw if r]
            if not raw:
                continue
            ntab += 1
            g, _ = grid_mod.expand(raw, mod._Cell)
            if header_mod.is_ragged(g):
                ragged += 1
        d['tables'] = ntab
        d['tables_ragged'] = ragged

    # sanitize — 치환이 실제로 일어났으면 글자 수가 늘어야 한다
    esc = [a for a in actions if a.get('action') == 'escape']
    d['escape_ops'] = len(esc)
    d['escape_count'] = sum(a.get('count', 0) for a in esc)
    d['escape_chars_added'] = sum(a.get('chars_added', 0) for a in esc)
    return d


def _structure(mod, source, doc_group, drop_empty, doc=None):
    """문서군별 구조화. (structured dict, actions)."""
    wanted = STRUCTURED.get(doc_group)
    if not wanted:
        return None, []

    out = {}
    actions = []

    if 'exchange' in wanted:
        # 거래소공시는 파서가 이미 라벨/값/섹션을 다 갈라 놓았다
        # (30,525행 전수 분류, 미분류 0건). HTML 을 다시 안 읽고
        # chunks 에서 만든다.
        from extract import exchange_html as xh
        st = xh.build(doc or {})
        if st['n_fields'] or st['notes']:
            out['exchange'] = st
            actions.append({
                'rule': 'S5_exchange', 'stage': 'extract',
                'action': 'structure', 'count': st['n_fields'],
                'sections': st['n_sections'], 'notes': len(st['notes']),
            })
        return (out or None), actions

    from normalize import tree, value, grid, table_router

    b = mod._TreeBuilder()
    b.feed(source)
    root = b.root

    if 'financials' in wanted:
        from extract import financials as fin
        groups = [fin.finalize(g) for g in fin.extract_groups(
            root, tree, value, grid_mod=grid, cell_cls=mod._Cell,
            router=table_router)]
        if groups:
            out['financials'] = groups
            low = [g for g in groups if g['parse_confidence'] == 'low']
            actions.append({
                'rule': 'S5_financials', 'stage': 'extract',
                'action': 'structure', 'count': len(groups),
                'low_confidence': len(low),
                'aclasses': sorted(set(g['aclass'] for g in groups)),
            })
            if low:
                actions.append({
                    'rule': 'S5_low_confidence', 'stage': 'extract',
                    'action': 'demote', 'severity': 'warn',
                    'count': len(low),
                    'reasons': sorted(set(r for g in low
                                          for r in g['confidence_reasons'])),
                })

    if 'acode' in wanted:
        from extract import acode as ac
        facts = ac.extract_facts(root, tree, value, grid_mod=grid,
                                 cell_cls=mod._Cell)
        if facts:
            out['acode_facts'] = facts
            actions.append({
                'rule': 'S5_acode', 'stage': 'extract',
                'action': 'structure', 'count': len(facts),
            })

    return (out or None), actions


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

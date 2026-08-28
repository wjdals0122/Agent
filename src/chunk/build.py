# -*- coding: utf-8 -*-
"""doc.json → 청크. **자족적인 조각**을 만드는 것이 목적이다.

────────────────────────────────────────────────────────────────────────
왜 이 파일이 필요한가 — 실측
────────────────────────────────────────────────────────────────────────
숫자가 실린 표 청크 43,321개(표본 400문서)를 재 보니

    단위가 청크 안에 있음        19.7%
    단위가 **이웃 청크에만** 있음 78.9%   ← 순진하게 쪼개면 끊긴다
    어디서도 못 찾음              1.4%

즉 그냥 조각내면 **열에 여덟은 단위를 잃는다.** 에이전트가 그 조각을
검색해 오면 `88,773,116` 을 보고 원인지 천원인지 주인지 알 수 없다.
숫자를 자신 있게 답하되 단위가 틀리는 것이 최악이다.

1단계에서 잰 E6 가 정확히 이 문제였다 —
`(단위 : 천원)` 캡션은 **다음 형제 표**에 귀속된다(뒤에만 54.8% vs
앞에만 0.2%). 그 사실을 5단계에서 {XBRL} 구조화 경로에만 반영하고
청크 경로에는 반영하지 않았다. 여기서 붙인다.

────────────────────────────────────────────────────────────────────────
자족성 규칙
────────────────────────────────────────────────────────────────────────
청크 하나만 읽고도 아래를 알 수 있어야 한다.
    누가       회사명 / 종목코드
    언제       접수일 / 보고서명 / 기수·기간
    무엇을     섹션 경로 (제목 계층)
    어떤 단위  단위 문자열
    믿을만한가 parse_confidence

`text` 는 임베딩·LLM 에 그대로 가는 문자열이고, 위 정보가 **본문 안에**
들어간다. 메타데이터 필드로만 두면 임베딩이 못 본다.

────────────────────────────────────────────────────────────────────────
각주(※)와 기수 — 표와 같은 조각에 들어가야 하는 나머지 둘
────────────────────────────────────────────────────────────────────────
**각주**: `※ 상기 금액은 …` 은 바로 **앞 표**의 단서다(실측: 앞에만 31.8% vs
뒤에만 10.0%). 그냥 문단으로 흘리면 표와 다른 조각으로 갈라져, 표만 검색해 온
에이전트는 단서를 못 본다. 그래서 **바로 앞 조각이 표일 때만** 그 표에 붙인다 —
각주의 45.2%는 양옆에 진짜 표가 없는 그냥 주석이라, 무조건 붙이면 없는 귀속을
만들어낸다(E6 실측).

**기수**: `제 17 기 1분기말` 이 언제인지는 다른 표에 적혀 있다. 3단계에서
문서마다 만든 사전(`doc['periods']`, extract/periods.py)을 여기서 조각에 물려,
조각 하나만 봐도 **언제의 숫자인지** 알 수 있게 한다. 못 이은 기수는 날짜 없이
라벨만 남는다 — 지어내지 않는다.
"""
import re

from extract import periods as per

__all__ = ['build_chunks', 'RE_UNIT_CAPTION', 'RE_NUMERIC_CELL',
           'RE_FOOTNOTE']

RE_UNIT_CAPTION = re.compile(r'\(\s*단\s*위\s*[:：]\s*([^)]*)\)')
# 본문 어디든 단위임이 분명한 형태. 맨 '원'·'주'·'건' 은 넣지 않는다 —
# 한글에는  가 안 먹어서 '주요'·'원활'·'사건' 에 걸린다.
RE_UNIT_ANY = re.compile(
    r'단\s*위\s*[:：]|\(단위|\(원\)|\(주\)|\(%\)|백만원|천원|억원|만원|조원'
    r'|원\)|주\)|％|%\)|미\s*달러|USD|천주|백만주')
RE_NUMERIC_CELL = re.compile(r'^-?\(?[\d,]{4,}\)?$')
# 기수 표기. **어디서 끊을지**는 extract/periods.py 가 정한다 — 사전을 만든
# 규칙과 조각에서 찾는 규칙이 다르면 같은 문서에서 두 답이 나온다.
# ('제 17 기' 로만 끊으면 사전의 '제 17 기 1분기말' 과 안 이어진다.)
from extract.periods import RE_PERIOD_FULL as RE_PERIOD
# E6 각주. 정책(E6_footnote_caption)과 같은 모양이어야 한다.
RE_FOOTNOTE = re.compile(r'^\s*[※*＊]')

# 표가 이보다 길면 쪼갠다. 쪼갤 때 헤더와 문맥을 **매 조각에 다시 붙인다** —
# 표 중간을 잘라 놓고 헤더를 안 주면 열이 무엇인지 알 수 없다.
MAX_TABLE_ROWS = 40


def _numeric_ratio(rows):
    n = c = 0
    for r in rows:
        for x in r:
            if not (x or '').strip():
                continue
            n += 1
            if RE_NUMERIC_CELL.match(x.strip()):
                c += 1
    return (c / n) if n else 0.0


def build_chunks(payload, part, doc_meta):
    """doc.json 의 part 하나 → 청크 목록.

    payload  : doc.json 최상위 (doc_id, corp_name … )
    part     : parts[i]
    doc_meta : manifest 에서 온 부가 정보 (없으면 payload 로 대체)
    """
    doc = part.get('doc') or {}
    chunks = doc.get('chunks') or []
    structured = doc.get('structured') or {}
    # E7 사전 — 3단계에서 이 문서 안의 날짜로만 만든 것이다.
    pmap = (doc.get('periods') or {}).get('map') or {}

    # {XBRL} 그룹의 확신도를 표 단위로 물려주기 위한 색인.
    # 5단계에서 매긴 등급을 청크까지 끌고 와야 에이전트가 쓸 수 있다.
    fin_conf = {}
    for g in structured.get('financials') or []:
        for cap in (g.get('caption_lines') or []):
            fin_conf[cap.strip()] = g

    out = []
    section = []          # 제목 계층
    unit = None           # 현재 유효한 단위
    unit_src = None
    periods = []          # 최근에 본 기수 표기

    def period_dates():
        """이 조각에 걸린 기수 표기 → 날짜. 못 이은 것은 넣지 않는다."""
        got = {}
        for lab in periods:
            v = per.fmt(per.resolve(pmap, lab))
            if v:
                got[lab] = v
        return got

    def ctx_header(extra=None, pdates=None):
        """청크 본문 맨 앞에 붙는 문맥 줄. 임베딩이 보게 하려고 본문에 넣는다."""
        bits = [doc_meta.get('corp_name') or payload.get('corp_name') or '']
        if doc_meta.get('report_nm'):
            bits.append(doc_meta['report_nm'])
        if doc_meta.get('rcept_dt'):
            bits.append(doc_meta['rcept_dt'])
        line = ' · '.join(x for x in bits if x)
        parts_ = ['[%s]' % line] if line else []
        if section:
            parts_.append('> ' + ' > '.join(section))
        if unit:
            parts_.append('(단위: %s)' % unit)
        # E7 — 기수가 언제인지. 라벨만 있으면 조각 하나로는 답할 수 없다.
        if pdates:
            parts_.append('(' + ' · '.join('%s = %s' % (k, v)
                                           for k, v in pdates.items()) + ')')
        if extra:
            parts_.append(extra)
        return '\n'.join(parts_)

    def emit(kind, body, **kw):
        pdates = period_dates()
        text = ctx_header(kw.pop('extra', None), pdates)
        text = (text + '\n' + body) if text else body
        rec = {
            'chunk_id': '%s#%04d' % (part.get('part_key'), len(out)),
            'doc_id': payload.get('doc_id'),
            'part_key': part.get('part_key'),
            'doc_group': payload.get('doc_group'),
            'corp_code': payload.get('corp_code'),
            'corp_name': doc_meta.get('corp_name') or payload.get('corp_name'),
            'rcept_dt': doc_meta.get('rcept_dt'),
            'report_nm': doc_meta.get('report_nm'),
            'is_correction': payload.get('is_correction'),
            'section_path': list(section),
            'unit': unit,
            'unit_source': unit_src,
            'periods': list(periods),
            'period_dates': pdates,
            'kind': kind,
            'text': text,
            'n_chars': len(text),
        }
        rec.update(kw)
        # 숫자가 실린 표인데 단위를 어디서도 못 찾았으면 **표시한다.**
        # 다른 절의 단위를 끌어다 붙이는 것은 단위를 추측하는 일이고,
        # 그게 이 파이프라인이 막으려는 실패다. 지어내지 않고 모른다고
        # 적어 두면 에이전트가 "단위가 명시되지 않았다"고 답할 수 있다.
        if rec['kind'] == 'table' and (rec.get('numeric_ratio') or 0) >= 0.2:
            known = bool(rec.get('unit')) or bool(RE_UNIT_ANY.search(text))
            rec['unit_known'] = known
        out.append(rec)

    buf = []          # 연속된 문단·키값을 모아 하나로

    def flush_text():
        if not buf:
            return
        emit('text', '\n'.join(buf))
        buf.clear()

    for c in chunks:
        kind = c[0]

        if kind == 'h':
            flush_text()
            lvl = max(1, min(int(c[1]), 6))
            section = section[:lvl - 1] + [c[2]]
            # 제목이 바뀌면 단위 문맥은 끊는다. 앞 절의 단위를
            # 다음 절 숫자에 물려주면 틀린 단위를 자신 있게 말하게 된다.
            unit, unit_src = None, None
            periods = []
            continue

        if kind == 'p':
            text = c[1] or ''
            # E6 각주 — 바로 앞 조각이 표일 때만 그 표에 붙인다.
            # buf 가 비어 있어야 '바로 앞'이다. 사이에 문단이 끼면 그 각주는
            # 표의 단서가 아니라 문단의 일부다. 붙일 데가 없으면 그냥
            # 문단으로 흘린다 — 각주의 45.2%가 그 경우다(1단계 실측).
            if (RE_FOOTNOTE.match(text) and not buf and out
                    and out[-1]['kind'] == 'table'):
                tgt = out[-1]
                tgt['text'] = tgt['text'] + '\n' + text
                tgt['n_chars'] = len(tgt['text'])
                tgt.setdefault('footnotes', []).append(text)
                # 각주가 단위를 들고 있는 경우가 있다 — 표에 물려준다.
                if tgt.get('kind') == 'table' and not tgt.get('unit_known'):
                    if RE_UNIT_ANY.search(text):
                        tgt['unit_known'] = True
                        tgt['unit_source'] = tgt.get('unit_source') or 'footnote'
                continue
            m = RE_UNIT_CAPTION.search(text)
            if m:
                # E6: 단위 캡션은 **다음** 표에 귀속된다 (실측 54.8% vs 0.2%)
                unit = m.group(1).strip()
                unit_src = 'preceding_caption'
                # 캡션만 있는 줄은 따로 청크로 만들지 않는다 — 문맥으로 흡수
                if RE_UNIT_CAPTION.sub('', text).strip():
                    buf.append(text)
                continue
            if RE_PERIOD.search(text):
                for pm in RE_PERIOD.finditer(text):
                    lab = re.sub(r'\s+', ' ', pm.group(0))
                    if lab not in periods:
                        periods.append(lab)
            buf.append(text)
            continue

        if kind == 'kv':
            parts_ = list(c[1])
            val = c[2]
            buf.append('- %s: %s' % (' > '.join(parts_),
                                     val if val is not None else '(해당없음)'))
            continue

        if kind == 't':
            flush_text()
            headers, rows = list(c[1]), [list(r) for r in c[2]]
            # 헤더에 단위가 이미 있으면 캡션보다 그쪽이 정확하다
            hdr_txt = ' '.join(headers)
            hm = RE_UNIT_CAPTION.search(hdr_txt)
            local_unit, local_src = (hm.group(1).strip(), 'table_header') \
                if hm else (unit, unit_src)

            # 기수 표기를 헤더에서 걷는다
            for h in headers:
                for pm in RE_PERIOD.finditer(h or ''):
                    lab = re.sub(r'\s+', ' ', pm.group(0))
                    if lab not in periods:
                        periods.append(lab)

            conf = None
            for cap, g in fin_conf.items():
                if cap and cap in ' '.join(section):
                    conf = g.get('parse_confidence')
                    break

            saved = (unit, unit_src)
            unit, unit_src = local_unit, local_src
            for start in range(0, max(1, len(rows)), MAX_TABLE_ROWS):
                part_rows = rows[start:start + MAX_TABLE_ROWS]
                body = _render_table(headers, part_rows)
                extra = None
                if len(rows) > MAX_TABLE_ROWS:
                    extra = '(표 %d–%d행 / 전체 %d행)' % (
                        start + 1, start + len(part_rows), len(rows))
                emit('table', body,
                     extra=extra,
                     n_rows=len(part_rows),
                     n_cols=len(headers),
                     numeric_ratio=round(_numeric_ratio(part_rows), 3),
                     parse_confidence=conf)
            unit, unit_src = saved
            continue

    flush_text()
    return out


def _render_table(headers, rows):
    def esc(x):
        return (x or '').replace('|', '\\|').replace('\n', ' ')
    lines = ['| ' + ' | '.join(esc(h) for h in headers) + ' |',
             '| ' + ' | '.join('---' for _ in headers) + ' |']
    n = len(headers)
    for r in rows:
        cells = [esc(x) for x in r] + [''] * (n - len(r))
        lines.append('| ' + ' | '.join(cells[:n]) + ' |')
    return '\n'.join(lines)

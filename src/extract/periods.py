# -*- coding: utf-8 -*-
r"""E7 — 기수(`제 N 기`) → 실제 날짜. 5단계 구조화 경로.

────────────────────────────────────────────────────────────────────────
문제
────────────────────────────────────────────────────────────────────────
DART 서식은 표 **헤더**에 `제 17 기 1분기말` 까지만 적고, 그 기수가 언제인지는
**다른 표(캡션표)** 에 적는다.

    TABLE#0  캡션표   재무상태표
                     제 17 기 1분기말 2023.03.31 현재
                     제 16 기말      2022.12.31 현재
    TABLE#1  데이터표 | 제 17 기 1분기말 | 제 16 기말 |
                     | 자산 …          | 2,227,819,930 |

md 로 읽을 때는 위아래라 사람이 잇는다. 조각으로 쪼개는 순간 헤더만 실린
조각이 남고, `제 17 기 1분기말` 이 언제인지 아는 방법이 사라진다.
1단계 실측: 기수 표기 **155,048건**, 이걸 날짜에 잇는 코드는 0곳이었다.

────────────────────────────────────────────────────────────────────────
방법 — 문서 안에서만 잇는다
────────────────────────────────────────────────────────────────────────
`제 N 기…` 와 날짜가 **같은 줄**에 있는 곳이 그 문서의 사전이다. 그 사전으로
문서 전체의 기수 표기를 해석한다. 문서 밖(회사의 결산월 같은 것)에서 날짜를
끌어오지 않는다 — 그건 추론이고, 이 파이프라인이 막으려는 실패다.

못 이은 기수는 **못 이었다고 남긴다**(`unresolved`). 0으로 채우거나 빈칸으로
두면 "안 재봤다"와 "없다"가 구분되지 않는다 (절대 규칙 2).

`extract/financials.py` 의 `extract_periods` 가 {XBRL} 캡션표에서 쓰던 바로 그
규칙이다. 두 벌로 갈라 두면 한쪽만 고치게 되므로 여기로 모으고 financials 는
이걸 부른다 (절대 규칙 6).
"""
import re

__all__ = ['RE_PERIOD_LABEL', 'RE_PERIOD_FULL', 'RE_DATE', 'from_lines',
           'scan_body', 'label_of', 'resolve', 'fmt']

# "제 55 기", "제55기 1분기말" — 기수 표기. census(01_exception_census.py)와
# 정책 E7_missing_period_date 의 패턴과 같아야 한다. 다르면 같은 것을 세는
# 두 경로가 다른 답을 낸다.
RE_PERIOD_LABEL = re.compile(r'제\s*\d{1,3}\s*기')
RE_DATE = re.compile(r'(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})')

# 라벨 전체 모양. 날짜가 같은 줄에 없을 때 **어디서 끊을지**를 정한다.
# 첫 날짜 앞까지 끊는 규칙(from_lines)은 캡션 줄에서만 옳다 — 본문 줄에 쓰면
# '제 17 기 1분기 3개월 매출액은 …' 처럼 문장을 통째로 라벨로 삼는다.
# 그래서 본문에서는 기수 뒤에 **올 수 있는 접미사만** 받는다.
# 실측에서 나온 모양: 1분기/반기/3분기 · 말/초 · 3개월/누적.
RE_PERIOD_FULL = re.compile(
    r'제\s*\d{1,3}\s*기'
    r'(?:\s*\d{1,2}\s*분기|\s*반기)?'
    r'(?:\s*(?:기말|기초|말|초))?'
    r'(?:\s*(?:누적|\d{1,2}\s*개월))?')


def _iso(t):
    y, mo, d = t
    return '%04d-%02d-%02d' % (int(y), int(mo), int(d))


def _norm(s):
    return re.sub(r'\s+', '', s or '')


def label_of(text):
    """글자 안의 기수 라벨 하나를 정규화해서 돌려준다. 없으면 None.

    날짜가 같은 줄에 있으면 첫 날짜 앞까지(캡션 규칙), 없으면 접미사
    화이트리스트까지만 끊는다(본문 규칙). 두 규칙을 섞으면 문장이 라벨이 된다.
    """
    m = RE_PERIOD_LABEL.search(text or '')
    if not m:
        return None
    d0 = RE_DATE.search(text, m.start())
    if d0:
        lab = text[m.start():d0.start()]
    else:
        f = RE_PERIOD_FULL.match(text, m.start())
        lab = f.group(0) if f else m.group(0)
    return re.sub(r'\s+', ' ', lab).strip()


def from_lines(lines):
    """캡션 줄에서 (기수 라벨 → 날짜) 를 뽑는다.

    '제 17 기 1분기말 2023.03.31 현재' → label '제 17 기 1분기말',
    instant 2023-03-31. 기간형이면 start/end 둘 다 잡는다.

    ⚠ 캡션 줄 전용이다. 라벨을 **첫 날짜 앞까지**로 끊기 때문에 날짜가 없는
    본문 줄에 쓰면 문장이 라벨이 된다. 본문에는 scan_body 를 쓴다.
    원래 extract/financials.py 의 extract_periods 이며 동작은 그대로다.
    """
    out = []
    for line in lines:
        m = RE_PERIOD_LABEL.search(line)
        if not m:
            continue
        d0 = RE_DATE.search(line, m.start())
        label = line[m.start():d0.start()] if d0 else line[m.start():]
        label = re.sub(r'\s+', ' ', label).strip()
        dates = [_iso(d) for d in RE_DATE.findall(line)]
        rec = {'label': label, 'raw': line.strip()}
        if len(dates) >= 2:
            rec.update(kind='duration', start=dates[0], end=dates[1])
        elif len(dates) == 1:
            rec.update(kind='instant', date=dates[0])
        else:
            rec.update(kind='unknown')
        out.append(rec)
    return out


def scan_body(body):
    """문서 본문 → 기수 사전.

    body: 파서가 읽어낸 본문 (줄바꿈이 살아 있는 문자열).

    돌려주는 dict
        n_labels     기수 표기 **출현 수** (census 의 e7_period_labels 와 같은 축)
        n_distinct   서로 다른 기수 라벨 수
        n_resolved   날짜를 이은 라벨 수
        map          라벨 → {kind, date | start/end, …}
        unresolved   날짜를 못 이은 라벨 (앞 20개)

    사전은 **기수와 날짜가 같은 줄에 있는 곳**에서만 만든다. 같은 라벨이 여러
    줄에 나오면 먼저 나온 것을 쓴다 — 캡션표가 데이터표보다 앞에 온다.
    """
    body = body or ''
    lines = body.split('\n')
    n_labels = len(RE_PERIOD_LABEL.findall(body))

    # ① 사전 — 날짜가 같은 줄에 있는 줄만 먹인다 (캡션 규칙이 옳은 자리)
    dated = {}
    for rec in from_lines([l for l in lines if RE_DATE.search(l)]):
        if rec.get('kind') not in ('instant', 'duration'):
            continue
        key = _norm(rec['label'])
        if key and key not in dated:
            dated[key] = rec

    # ② 문서에 실제로 나온 라벨 목록 (본문 규칙으로 끊는다)
    seen = []
    seen_set = set()
    for m in RE_PERIOD_FULL.finditer(body):
        lab = re.sub(r'\s+', ' ', m.group(0)).strip()
        key = _norm(lab)
        if key not in seen_set:
            seen_set.add(key)
            seen.append(lab)

    out_map = {}
    unresolved = []
    for lab in seen:
        key = _norm(lab)
        rec = dated.get(key)
        if rec is None:
            # 접두 일치: 헤더 '제 17 기 1분기 3개월' ↔ 캡션 '제 17 기 1분기'.
            # 가장 긴 접두사로 잇고 남는 꼬리는 측정 구분으로 남긴다 — 꼬리를
            # 버리면 3개월과 누적이 뒤섞인다 (financials._map_columns 와 같은 규칙).
            best = None
            for k in dated:
                if k and key.startswith(k) and (best is None or len(k) > len(best)):
                    best = k
            if best is not None:
                rec = dict(dated[best], matched=dated[best]['label'],
                           qualifier=key[len(best):])
        if rec is None:
            unresolved.append(lab)
            continue
        out_map[lab] = {k: v for k, v in rec.items() if k != 'raw'}

    return {'n_labels': n_labels,
            'n_distinct': len(seen),
            'n_resolved': len(out_map),
            'map': out_map,
            'unresolved': unresolved[:20]}


def fmt(rec):
    """기간 레코드 → 사람이 읽는 한 조각. 못 이은 것은 None."""
    if not rec:
        return None
    if rec.get('kind') == 'instant':
        return rec.get('date')
    if rec.get('kind') == 'duration':
        return '%s ~ %s' % (rec.get('start'), rec.get('end'))
    return None


def resolve(mapping, label):
    """사전에서 라벨을 찾는다. 정확 일치 → 최장 접두 일치.

    scan_body 가 사전을 만들 때 쓴 것과 **같은 규칙**이다. 조각을 만들 때
    다른 규칙으로 찾으면 같은 문서에서 두 답이 나온다.
    """
    if not mapping or not label:
        return None
    key = _norm(label)
    for k, v in mapping.items():
        if _norm(k) == key:
            return v
    best = None
    best_len = 0
    for k, v in mapping.items():
        nk = _norm(k)
        if nk and key.startswith(nk) and len(nk) > best_len:
            best, best_len = v, len(nk)
    return best

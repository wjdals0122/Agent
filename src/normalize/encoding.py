# -*- coding: utf-8 -*-
"""E3 — 인코딩. detect / handle 분리.

기존 동작
    BOM 을 떼고 UTF-8 로 시도, 실패하면 cp949 + errors='replace'.
    문서에 적힌 `charset` 은 **읽지 않는다.** exchange HTML 1,469건이
    전부 `charset=euc-kr` 이라고 선언하지만 실제 바이트는 UTF-8이다.

절대 규칙 4 — 파서에 bytes 를 넘기지 않는다
    반드시 여기를 지나 str 로 만든 뒤 넘긴다.

이 파일이 고치는 것 하나 — 삼키는 예외
    원본 `decode()` 는 cp949 폴백이 발동했는지, `errors='replace'` 가
    글자를 몇 개 U+FFFD 로 바꿨는지 **아무 데도 남기지 않는다.**
    파싱은 성공하고 글자만 조용히 사라진다. 절대 규칙 2 위반이다.
    → `decode()` 는 `(text, actions)` 를 돌려준다. 글자 결과는
      원본과 바이트 동일하고, 무슨 일이 있었는지가 추가로 남는다.
      기존 호출부를 위해 `decode_text()` 가 str 만 돌려준다.
"""

__all__ = ['detect', 'decode', 'decode_text', 'UTF8_BOM']

UTF8_BOM = b'\xef\xbb\xbf'
REPLACEMENT = '�'


def detect(raw_bytes, declared=None):
    """판정만 한다. 부작용 없음. 진단 dict 를 돌려준다.

    declared: 문서가 스스로 주장하는 charset (있으면). 판정에 **쓰지
    않는다** — 어긋났다는 사실을 기록하기 위해서만 받는다.
    """
    has_bom = raw_bytes[:3] == UTF8_BOM
    body = raw_bytes[3:] if has_bom else raw_bytes
    try:
        body.decode('utf-8')
        actual = 'utf-8'
        ok = True
    except UnicodeDecodeError:
        actual = 'cp949'
        ok = False
    return {
        'has_bom': has_bom,
        'actual': actual,
        'utf8_clean': ok,
        'declared': declared,
        'declared_mismatch': bool(declared
                                  and declared.lower().replace('_', '-')
                                  not in ('utf-8', 'utf8')),
    }


def decode(raw_bytes, declared=None):
    """(text, actions) 를 돌려준다.

    text 는 원본 `decode()` 와 바이트 동일하다.
    actions 는 무슨 일이 있었는지의 기록이며, 아무 일도 없었으면 빈 리스트다.
    """
    actions = []
    if raw_bytes[:3] == UTF8_BOM:
        raw_bytes = raw_bytes[3:]
        actions.append({'rule': 'E3_utf8_bom', 'action': 'strip', 'count': 1})

    if declared and declared.lower().replace('_', '-') not in ('utf-8', 'utf8'):
        # 선언을 무시했다는 사실 자체를 남긴다.
        actions.append({'rule': 'E3_declared_charset_ignored',
                        'action': 'ignore', 'declared': declared,
                        'count': 1})
    try:
        return raw_bytes.decode('utf-8'), actions
    except UnicodeDecodeError as e:
        text = raw_bytes.decode('cp949', errors='replace')
        actions.append({
            'rule': 'E3_cp949_fallback',
            'action': 'decode_cp949_replace',
            'count': 1,
            'utf8_error_at': getattr(e, 'start', None),
            # errors='replace' 는 조용히 글자를 먹는다. 몇 개인지 남긴다.
            'replacement_chars': text.count(REPLACEMENT),
        })
        return text, actions


def decode_text(raw_bytes, declared=None):
    """기존 호출부용 — str 만 돌려준다. 원본 `decode()` 와 동일."""
    return decode(raw_bytes, declared)[0]

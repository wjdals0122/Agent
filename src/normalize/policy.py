# -*- coding: utf-8 -*-
"""예외 정책 엔진 — `config/exception_policy.yaml` 을 읽어 돌린다.

정책이 코드가 아니라 데이터다. 새 예외 케이스는 코드가 아니라 정책 파일과
`docs/exception_matrix.md` 에 **먼저** 적는다.

이 모듈이 보장하는 것
    · detect 는 부작용이 없다. 세거나 판정만 한다.
    · handle 은 `(결과, 조치기록)` 을 돌려준다. 결과를 안 바꾸는
      `count_only` 도 **기록은 반드시 남긴다** (절대 규칙 2).
    · 정책에 없는 규칙은 돌지 않는다. 코드에 숨은 분기가 없다.

count_only 가 대부분인 이유는 정책 파일 머리말 참조 — 기존 관대 파서가
이미 처리하고 있어서, 여기서 또 치환하면 이중 처리가 된다.
"""
import os
import re

__all__ = ['Policy', 'load', 'DEFAULT_PATH']

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'exception_policy.yaml')

_CACHE = {}


class Rule:
    __slots__ = ('id', 'title', 'stage', 'detect', 'handle', 'severity',
                 'record', 'measured', 'note', 'verify', '_rx')

    def __init__(self, d):
        self.id = d['id']
        self.title = d.get('title') or d['id']
        self.stage = d.get('stage')
        self.detect = d.get('detect') or {}
        self.handle = d.get('handle') or {}
        self.severity = d.get('severity', 'info')
        self.record = d.get('record', 'count')
        self.measured = d.get('measured') or {}
        self.note = d.get('note')
        self.verify = d.get('verify')
        self._rx = (re.compile(self.detect['pattern'])
                    if self.detect.get('type') == 'regex' else None)

    @property
    def wants_samples(self):
        return 'samples' in (self.record or '')

    def scan(self, text, max_samples=5):
        """regex 정책의 detect. 부작용 없음. 안 맞으면 None."""
        if self._rx is None:
            return None
        hits = list(self._rx.finditer(text))
        if not hits:
            return None
        out = {'rule': self.id, 'stage': self.stage, 'action': 'detect',
               'severity': self.severity, 'count': len(hits)}
        if self.wants_samples:
            out['samples'] = _samples(self.id, text, hits, max_samples)
        return out

    def apply(self, text):
        """handle 층. 항상 (결과, 조치기록) 을 돌려준다."""
        kind = self.handle.get('type')
        found = self.scan(text)

        if kind in (None, 'count_only', 'record_only', 'defer'):
            # 원문을 바꾸지 않는다. 그래도 기록은 남긴다.
            return text, ([found] if found else [])

        if kind == 'escape':
            to = self.handle['to']
            n = 0

            def sub(m):
                nonlocal n
                n += 1
                return to

            out = self._rx.sub(sub, text)
            if not n:
                return text, []
            return out, [{'rule': self.id, 'stage': self.stage,
                          'action': 'escape', 'to': to, 'count': n,
                          'severity': self.severity,
                          'chars_added': len(out) - len(text)}]

        raise ValueError('%s: 모르는 handle.type=%r' % (self.id, kind))


def _samples(rule_id, text, hits, k):
    """규칙에 맞는 모양으로 표본을 뽑는다."""
    out = []
    if rule_id == 'E2_bare_lt':
        # 꺾쇠 캡션은 통짜로 보여줘야 뜻이 보인다
        for m in re.finditer(r'<[^<>\n]{0,60}>', text):
            if re.match(r'<(?![/?!]|[A-Za-z])', m.group(0)):
                out.append(m.group(0))
                if len(out) >= k:
                    break
        return out
    for m in hits[:k]:
        i = m.start()
        out.append(text[i:i + 20].replace('\n', ' '))
    return out


class Policy:
    def __init__(self, data, path=None):
        self.version = data.get('version')
        self.path = path
        self.rules = [Rule(d) for d in (data.get('policies') or [])]
        self.by_id = {r.id: r for r in self.rules}

    def for_stage(self, stage):
        return [r for r in self.rules if r.stage == stage]

    def runnable(self, stage):
        """지금 실제로 돌릴 수 있는 규칙 — regex detect 를 가진 것만."""
        return [r for r in self.for_stage(stage) if r._rx is not None]

    def run_stage(self, stage, text):
        """(결과, 조치기록 목록). 정책에 적힌 순서대로 돈다."""
        actions = []
        for r in self.runnable(stage):
            text, acts = r.apply(text)
            actions.extend(acts)
        return text, actions

    def __len__(self):
        return len(self.rules)


def load(path=None):
    """정책을 읽는다. 같은 경로는 캐시한다 (워커마다 한 번)."""
    path = path or DEFAULT_PATH
    if path in _CACHE:
        return _CACHE[path]
    import yaml
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    pol = Policy(data, path)
    _CACHE[path] = pol
    return pol

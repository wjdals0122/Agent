"""라벨 없는 검색을 채점하기 위한 약한 정답 기준.

청크마다 사람이 붙인 정답은 없다. 대신 질문에 두 가지 기대치를 적어두고 그걸로 채점한다.

- `company`   기대 회사. 상위 결과가 그 회사에서 나왔는가 (개체를 제대로 잡는가)
- `expect_any` 기대 섹션 키워드 목록. 상위 결과의 섹션 경로나 문서 제목에 하나라도 걸리는가

둘 다 완벽한 정답은 아니다. 특히 `expect_any`는 표현이 조금만 달라도 놓친다. 그러니
절대 점수보다 **설정을 바꿨을 때 같은 기준으로 오르내리는 방향**을 보는 데 쓴다.
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def norm(s: str | None) -> str:
    return _WS.sub("", s or "")


def section_ok(hit, question) -> bool:
    patterns = question.get("expect_any") or []
    if not patterns:
        return False
    text = norm(getattr(hit, "section_path", "")) + "|" + norm(getattr(hit, "document_title", ""))
    return any(norm(p) in text for p in patterns)


def company_ok(hit, question) -> bool | None:
    want = question.get("company")
    if not want:
        return None
    return norm(getattr(hit, "company", "")) == norm(want)


def entity_coverage(hits, question) -> float | None:
    """비교 질의 전용. 기대한 회사들이 상위 k 안에 몇 곳이나 등장했는가.

    이 값이 1.0 미만이면 생성기가 한쪽 근거만 보고 비교를 지어낸다. 비교 질의에서
    가장 먼저 봐야 하는 수치다.
    """
    want = question.get("expect_companies") or []
    if len(want) < 2:
        return None
    seen = {norm(getattr(h, "company", "")) for h in hits}
    return round(sum(1 for w in want if norm(w) in seen) / len(want), 4)


def score_hits(hits, question) -> dict:
    """상위 k건에 대한 문항 단위 지표."""
    if not hits:
        return {"section_at1": 0.0, "section_atk": 0.0, "section_mrr": 0.0,
                "company_at1": None, "company_atk": None, "strict_at1": None,
                "entity_coverage": entity_coverage(hits, question)}

    sec = [section_ok(h, question) for h in hits]
    comp = [company_ok(h, question) for h in hits]
    has_company = comp[0] is not None

    mrr = 0.0
    for i, ok in enumerate(sec, 1):
        if ok:
            mrr = 1.0 / i
            break

    return {
        "section_at1": float(sec[0]),
        "section_atk": float(any(sec)),
        "section_mrr": round(mrr, 4),
        "company_at1": float(comp[0]) if has_company else None,
        "company_atk": round(sum(comp) / len(comp), 4) if has_company else None,
        # 회사도 맞고 섹션도 맞은 1위 — 실제로 답을 만들 수 있는 상태에 가장 가깝다
        "strict_at1": float(comp[0] and sec[0]) if has_company else None,
        "entity_coverage": entity_coverage(hits, question),
    }


def aggregate(per_question: list[dict]) -> dict:
    """None(해당 없음)은 빼고 평균낸다."""
    keys = ["section_at1", "section_atk", "section_mrr", "company_at1", "company_atk",
            "strict_at1", "entity_coverage"]
    out = {}
    for key in keys:
        vals = [q[key] for q in per_question if q.get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
        out[f"{key}_n"] = len(vals)
    return out

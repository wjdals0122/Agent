"""하이브리드 검색기 — 하드필터 우선 + BM25/dense RRF 융합.

【설계의 핵심: 하드필터를 먼저 건다】
회사·연도로 후보를 좁힌 **뒤** 그 안에서만 점수를 낸다.
전체에서 top-k 를 뽑고 나중에 거르면, 회사 하나로 좁혔을 때 0건이 된다.

【코드 체계 주의 — 치명 A】
id_map.parquet 의 corp_code 컬럼은 실제로 6자리 종목코드다.
universe.csv 의 corp_code 는 8자리 DART 고유번호로 값 체계가 다르다.
여기서는 로드하자마자 stock_code 로 이름을 바꿔 혼동을 코드에서 제거한다.
필터 파라미터로 corp_code(8자리)를 받지 않는다.

【base_year 주의】
id_map 의 base_year 는 **접수 연도**(receipt_no 앞 4자리)다. 사업연도가 아니다.
2024 사업연도 사업보고서는 2025년에 접수되므로 base_year=2025 다.
사업연도로 거르려면 report_nm 의 '(2024.12)' 를 봐야 한다 — fiscal_year 인자를 쓴다.
"""
from __future__ import annotations

import json
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from src.eval.chunk_store import ChunkStore
from src.index import paths
from src.search.aliases import resolve_company
from src.search.bm25 import BM25

MASK = paths.ROOT / "data" / "index" / "latest_mask.npz"
MANIFEST = paths.ROOT / "corpus" / "manifest.jsonl"
RRF_K = 60          # 표준값. 1위가 1/61, 200위가 1/260
TOP_M = 200         # 각 리스트에서 이 순위 안에 든 것만 융합에 기여한다
PER_DOC = 2         # 한 문서가 결과를 독점하지 못하게
POOL = 50           # 리랭커(단계 4) 입력이 될 후보 수
_NONDIGIT = re.compile(r"[^0-9]")
_FY = re.compile(r"\((\d{4})[.\-/](\d{1,2})\)")


def _ymd(v):
    """'2024-03-17' / '20240317' 어느 쪽으로 와도 'YYYYMMDD' 로 통일한다."""
    if v is None:
        return None
    s = _NONDIGIT.sub("", str(v))
    return s or None


class HybridSearch:
    def __init__(self, device: str = "cuda", gpu: int = 0, verbose: bool = True) -> None:
        self.device = device
        self.gpu = gpu
        self._verbose = verbose
        t0 = time.time()

        idm = pd.read_parquet(paths.ID_MAP)
        # 치명 A: 이름 충돌을 여기서 없앤다
        idm = idm.rename(columns={"corp_code": "stock_code"})
        idm["stock_code"] = idm["stock_code"].astype(str).str.zfill(6)
        idm["base_year"] = idm["base_year"].astype(np.int32)
        idm["rcept_dt"] = idm["rcept_dt"].astype(str)
        idm["receipt_no"] = idm["receipt_no"].astype(str)

        man = pd.DataFrame([json.loads(l) for l in open(MANIFEST, encoding="utf-8")])
        man = man[["rcept_no", "report_nm", "doc_subtype"]].rename(
            columns={"rcept_no": "receipt_no"})
        man["receipt_no"] = man["receipt_no"].astype(str)
        idm = idm.merge(man, on="receipt_no", how="left")
        if idm["report_nm"].isna().any():
            raise SystemExit("manifest 조인 실패 — report_nm 결측")

        self.n = len(idm)
        self.stock_code = idm["stock_code"].to_numpy()
        self.company = idm["company"].to_numpy()
        self.doc_group = idm["doc_group"].to_numpy()
        self.base_year = idm["base_year"].to_numpy()
        self.rcept_dt = idm["rcept_dt"].to_numpy()
        self.receipt_no = idm["receipt_no"].to_numpy()
        self.receipt_i = idm["receipt_no"].astype(np.int64).to_numpy()
        self.doc_id = idm["doc_id"].to_numpy()
        self.chunk_id = idm["chunk_id"].to_numpy()
        self.embed_sha1 = idm["embed_sha1"].to_numpy()
        self.report_nm = idm["report_nm"].to_numpy()
        # report_nm 의 '(2024.12)' 에서 사업연도를 뽑아둔다 (base_year 는 접수연도라 다르다)
        fy = idm["report_nm"].str.extract(_FY)[0]
        self.fiscal_year = fy.fillna(-1).astype(np.int32).to_numpy()

        z = np.load(MASK)
        self.is_latest = z["is_latest"]
        self.preserved = z["preserved"]
        self.series_code = z["series_code"]
        n_excl = int((~self.is_latest).sum())
        self._log(f"[correction] 구본 제외 {n_excl:,}행 · 보존 {int(self.preserved.sum()):,}행")
        if n_excl == 0:
            raise SystemExit("[correction] 제외 0건 — latest_mask 가 비었다. 중단")

        self.bm25 = BM25()
        self.store = ChunkStore()
        self._load_dense()
        self._load_model()
        self._log(f"[load] 총 {time.time() - t0:.1f}초")

    def _log(self, m: str) -> None:
        if self._verbose:
            print(m, flush=True)

    def _load_dense(self) -> None:
        t = time.time()
        arr = np.load(paths.DENSE, mmap_mode="r")
        if arr.shape != (self.n, paths.DIM):
            raise SystemExit(f"dense shape {arr.shape} != {(self.n, paths.DIM)}")
        if self.device == "cuda":
            import torch

            from src.eval import gpu_guard

            gpu_guard.apply_memory_fraction(self.gpu, None)
            self.torch = torch
            self.dense = torch.empty((self.n, paths.DIM), dtype=torch.float16, device="cuda")
            for i in range(0, self.n, 50_000):
                blk = np.asarray(arr[i:i + 50_000], dtype=np.float32)
                self.dense[i:i + 50_000] = torch.from_numpy(blk).to("cuda", torch.float16)
            self._log(f"[load] dense -> GPU fp16 "
                      f"{torch.cuda.memory_allocated() / 1e9:.2f}GB ({time.time() - t:.1f}초)")
        else:
            self.torch = None
            self.dense = np.asarray(arr, dtype=np.float32)
            self._log(f"[load] dense -> CPU fp32 ({time.time() - t:.1f}초)")

    def _load_model(self) -> None:
        t = time.time()
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel(
            paths.MODEL_NAME, use_fp16=True, normalize_embeddings=True,
            devices="cuda:0" if self.device == "cuda" else "cpu")
        self._log(f"[load] {paths.MODEL_NAME} ({time.time() - t:.1f}초)")

    # ---------------------------------------------------------------- 필터

    def _candidates(self, stock_code=None, base_year=None, fiscal_year=None,
                    doc_group=None, rcept_dt_from=None, rcept_dt_to=None,
                    include_superseded=False) -> np.ndarray:
        m = np.ones(self.n, dtype=bool)
        if not include_superseded:
            m &= self.is_latest
        if stock_code:
            codes = [stock_code] if isinstance(stock_code, str) else list(stock_code)
            m &= np.isin(self.stock_code, [str(c).zfill(6) for c in codes])
        if base_year is not None:
            ys = [base_year] if isinstance(base_year, int) else list(base_year)
            m &= np.isin(self.base_year, [int(y) for y in ys])
        if fiscal_year is not None:
            ys = [fiscal_year] if isinstance(fiscal_year, int) else list(fiscal_year)
            m &= np.isin(self.fiscal_year, [int(y) for y in ys])
        if doc_group:
            gs = [doc_group] if isinstance(doc_group, str) else list(doc_group)
            m &= np.isin(self.doc_group, gs)
        f, t = _ymd(rcept_dt_from), _ymd(rcept_dt_to)
        if f:
            m &= self.rcept_dt >= f
        if t:
            m &= self.rcept_dt <= t
        return np.flatnonzero(m)

    # ---------------------------------------------------------------- 점수

    def _dense_scores(self, query: str, cand: np.ndarray) -> np.ndarray:
        out = self.model.encode([query], batch_size=1, max_length=512,
                                return_dense=True, return_sparse=False,
                                return_colbert_vecs=False)
        q = np.asarray(out["dense_vecs"][0], dtype=np.float32)
        if self.torch is not None:
            qt = self.torch.from_numpy(q).to("cuda", self.torch.float16)
            s = (self.dense @ qt).float().cpu().numpy()
        else:
            s = self.dense @ q
        return s[cand]

    @staticmethod
    def _ranks(scores: np.ndarray, top_m: int) -> dict:
        """점수 상위 top_m 의 {후보 인덱스: 순위}. 0점 이하는 리스트에 없는 것으로 본다."""
        nz = np.flatnonzero(scores > 0)
        if nz.size == 0:
            return {}
        k = min(top_m, nz.size)
        part = nz[np.argpartition(-scores[nz], k - 1)[:k]]
        order = part[np.argsort(-scores[part])]
        return {int(i): r for r, i in enumerate(order, 1)}

    # ---------------------------------------------------------------- 검색

    def search(self, query: str, corp_name=None, stock_code=None, base_year=None,
               fiscal_year=None, doc_group=None, rcept_dt_from=None, rcept_dt_to=None,
               top_k: int = 5, pool: int = POOL, top_m: int = TOP_M,
               per_doc: int = PER_DOC, include_superseded: bool = False,
               **_ignored) -> dict:
        t0 = time.time()
        filters = {"corp_name": corp_name, "stock_code": stock_code,
                   "base_year": base_year, "fiscal_year": fiscal_year,
                   "doc_group": doc_group,
                   "rcept_dt_from": _ymd(rcept_dt_from), "rcept_dt_to": _ymd(rcept_dt_to),
                   "include_superseded": include_superseded}
        empty = {"query": query, "filters": filters, "results": [], "pool": [],
                 "n_candidates": 0, "n_pool": 0, "n_preserved_dropped": 0}

        if corp_name:
            r = resolve_company(corp_name)
            if r["status"] == "not_found":
                return {**empty, "status": "not_found",
                        "note": f"코퍼스 70개사에 '{corp_name}' 이 없다"}
            if r["status"] == "ambiguous":
                return {**empty, "status": "ambiguous", "candidates": r["candidates"],
                        "note": r.get("note", "회사가 특정되지 않는다")}
            stock_code = r["stock_code"]
            filters["stock_code"] = stock_code

        cand = self._candidates(stock_code, base_year, fiscal_year, doc_group,
                                rcept_dt_from, rcept_dt_to, include_superseded)
        if cand.size == 0:
            return {**empty, "status": "not_found", "note": "필터 조건에 맞는 문서가 없다"}

        bm = self.bm25.score(query, rows=cand)
        dn = self._dense_scores(query, cand)
        r_bm = self._ranks(bm, top_m)
        r_dn = self._ranks(dn, top_m)

        # RRF — 각 리스트의 top_m 안에 든 것만 기여한다.
        # BM25 0점(질의어 미포함)은 리스트에 없으므로 가짜 순위로 끼어들지 못한다.
        rrf = {}
        for d in (r_bm, r_dn):
            for i, rank in d.items():
                rrf[i] = rrf.get(i, 0.0) + 1.0 / (RRF_K + rank)
        if not rrf:
            return {**empty, "status": "not_found", "n_candidates": int(cand.size),
                    "note": "질의어를 포함하거나 의미가 가까운 청크가 없다"}

        idxs = list(rrf.keys())
        # 정렬 키: RRF 내림차순, 동점이면 접수번호 큰 쪽(최신본) 우선
        idxs.sort(key=lambda i: (-rrf[i], -int(self.receipt_i[cand[i]])))

        # 확정 4(개정 B) — preserved(정정 전 첨부) 조건부 드롭.
        # 같은 계열의 최신본이 **그보다 위 순위에** 이미 뽑혔을 때만 버린다.
        #   "매출액"   → 최신본이 위 → 같은 수치의 중복이므로 버린다
        #   "감사의견" → 정정 전이 위 → 최신본에 없는 내용이므로 남긴다
        # 계열 단위로 일괄 드롭하면 후자에서 유일한 근거가 사라진다(실측 확인).
        # 드롭된 행은 pool 자리를 차지하지 않는다 — 리랭커가 쓸 후보 수를 지키기 위해서다.
        picked = []          # (전체 row, 후보 인덱스)
        seen_sha = set()
        per = {}
        latest_series = set()
        n_dropped = 0
        for i in idxs:
            row = int(cand[i])
            sha = self.embed_sha1[row]
            if sha in seen_sha:                   # 같은 내용 중복 제거
                continue
            d = self.doc_id[row]
            if per.get(d, 0) >= per_doc:          # 문서당 상한
                continue
            if self.preserved[row] and int(self.series_code[row]) in latest_series:
                n_dropped += 1
                continue
            seen_sha.add(sha)
            per[d] = per.get(d, 0) + 1
            if not self.preserved[row]:
                latest_series.add(int(self.series_code[row]))
            picked.append((row, i))
            if len(picked) >= pool:
                break

        results = [self._render(r, i, rrf[i], bm, dn, k + 1)
                   for k, (r, i) in enumerate(picked[:top_k])]
        pool_out = [self._render(r, i, rrf[i], bm, dn, k + 1, content=False)
                    for k, (r, i) in enumerate(picked)]
        return {"status": "ok" if results else "not_found",
                "query": query, "filters": filters,
                "n_candidates": int(cand.size), "n_pool": len(picked),
                "n_preserved_dropped": n_dropped,
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
                "results": results, "pool": pool_out,
                "note": "" if results else "융합 후 남은 결과가 없다"}

    def _render(self, row: int, ci: int, rrf: float, bm, dn, rank: int,
                content: bool = True) -> dict:
        rec = self.store.get(row) if content else None
        src = {
            "company": str(self.company[row]),
            "stock_code": str(self.stock_code[row]),
            # 출처의 보고서명은 청크의 document_title 이 아니라 manifest 의 report_nm 을 쓴다
            "report_nm": str(self.report_nm[row]),
            "receipt_no": str(self.receipt_no[row]),
            "rcept_dt": str(self.rcept_dt[row]),
            "doc_group": str(self.doc_group[row]),
            "base_year": int(self.base_year[row]),
            "fiscal_year": int(self.fiscal_year[row]),
        }
        if self.preserved[row]:
            d = src["rcept_dt"]
            src["note"] = (f"정정 전 첨부본(접수 {d[:4]}-{d[4:6]}-{d[6:8]}). "
                           "이후 정정본이 존재하나 이 섹션은 정정본에 없음")
        out = {"rank": rank, "row": row, "chunk_id": str(self.chunk_id[row]),
               "doc_id": str(self.doc_id[row]), "rrf": round(float(rrf), 6),
               "bm25": round(float(bm[ci]), 4), "dense": round(float(dn[ci]), 4),
               "preserved": bool(self.preserved[row]), "source": src}
        if rec is not None:
            out["section_path"] = " > ".join(rec.get("section_path") or [])
            out["content"] = rec.get("content") or ""
        return out


_INSTANCE = None


def get_searcher(**kw) -> HybridSearch:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = HybridSearch(**kw)
    return _INSTANCE


def search_disclosure(query: str, **kw) -> dict:
    return get_searcher().search(query, **kw)

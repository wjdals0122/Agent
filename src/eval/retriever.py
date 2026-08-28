"""bge-m3 하이브리드 검색기 (dense + sparse lexical) + 하드필터.

bge-m3는 대칭 모델이라 질의에 `query: ` 같은 접두사를 붙이지 않는다 (README 주의사항).
점수는 dense 코사인과 sparse lexical 내적을 가중합한다. 두 점수는 스케일이 달라서
가중치는 실험으로 잡아야 하고, 그래서 결과에 두 성분을 따로 남긴다.

torch를 쓰기 전에 gpu_guard.select_gpu()를 먼저 부를 것. 이 모듈은 torch를 지연 import 한다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import numpy as np
import pyarrow.parquet as pq
import scipy.sparse as sp

from src.eval import gpu_guard
from src.eval.chunk_store import ChunkStore
from src.index import paths


@dataclass
class Hit:
    rank: int
    row: int
    score: float
    dense: float
    sparse: float
    chunk_id: str
    company: str
    corp_code: str
    doc_group: str
    receipt_no: str
    document_title: str
    section_path: str
    content: str


@dataclass
class SearchResult:
    query: str
    hits: list[Hit]
    encode_ms: float
    search_ms: float
    n_candidates: int
    filters: dict = field(default_factory=dict)
    entities: list = field(default_factory=list)


def _join_section(value) -> str:
    if isinstance(value, list):
        return " > ".join(str(x) for x in value)
    return str(value or "")


class Retriever:
    def __init__(
        self,
        gpu: int = 0,
        device: str = "cuda",
        use_sparse: bool = True,
        latest_only: bool = False,
        max_temp: float = 80.0,
        resume_temp: float = 70.0,
        verbose: bool = True,
    ) -> None:
        self.gpu = gpu
        self.device = device
        self.use_sparse = use_sparse
        # True면 정정 재제출로 밀려난 옛 정기보고서를 후보에서 뺀다.
        # 실험 중 ret.latest_only = True/False로 바로 바꿔 비교할 수 있다.
        self.latest_only = latest_only
        self.max_temp = max_temp
        self.resume_temp = resume_temp
        self._verbose = verbose

        t0 = time.time()
        idm = pq.read_table(
            paths.ID_MAP, columns=["corp_code", "company", "doc_group", "base_year", "doc_id"]
        )
        self.corp_code = np.asarray(idm.column("corp_code").to_pylist(), dtype="<U6")
        self.company = np.asarray(idm.column("company").to_pylist(), dtype="<U24")
        self.doc_group = np.asarray(idm.column("doc_group").to_pylist(), dtype="<U10")
        self.base_year = idm.column("base_year").to_numpy().astype(np.int32)
        self.doc_id = np.asarray(idm.column("doc_id").to_pylist(), dtype="<U26")
        self.n = len(self.corp_code)
        # 회사명 -> 종목코드. 사용자가 "삼성전자"처럼 이름으로 거는 필터를 풀어준다.
        self.name_to_code = dict(zip(self.company.tolist(), self.corp_code.tolist()))
        self._log(f"[load] id_map {self.n:,}행 ({time.time() - t0:.1f}초)")

        self.store = ChunkStore()
        n_stale = int(self.store.superseded.sum())
        self._log(f"[load] 정정 재제출로 밀려난 옛 정기보고서 {n_stale:,}행 "
                  f"(latest_only={latest_only})")
        self._load_dense()

        self.sparse = None
        if use_sparse:
            t = time.time()
            self.sparse = sp.load_npz(paths.SPARSE)
            self._log(
                f"[load] sparse {self.sparse.shape} nnz={self.sparse.nnz:,} "
                f"({time.time() - t:.1f}초)"
            )

        self._load_model()

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(msg, flush=True)

    # ---------- 로딩 ----------

    def _load_dense(self) -> None:
        t = time.time()
        arr = np.load(paths.DENSE, mmap_mode="r")
        if arr.shape != (self.n, paths.DIM):
            raise SystemExit(f"dense shape {arr.shape}, 기대 {(self.n, paths.DIM)}")

        if self.device == "cuda":
            import torch

            frac = gpu_guard.apply_memory_fraction(self.gpu, None)
            # fp16이면 614,693x1024가 1.26GB. 랭킹 목적으로 정밀도는 충분하다.
            self.dense = torch.empty((self.n, paths.DIM), dtype=torch.float16, device="cuda")
            step = 50_000
            for i in range(0, self.n, step):
                blk = np.asarray(arr[i : i + step], dtype=np.float32)
                self.dense[i : i + step] = torch.from_numpy(blk).to("cuda", torch.float16)
            gb = torch.cuda.memory_allocated() / 1e9
            self._log(
                f"[load] dense -> GPU {self.gpu} fp16 {gb:.2f}GB "
                f"(메모리 상한 {frac:.0%}, {time.time() - t:.1f}초)"
            )
        else:
            self.dense = np.asarray(arr, dtype=np.float32)
            self._log(f"[load] dense -> CPU fp32 ({time.time() - t:.1f}초)")

    def _load_model(self) -> None:
        t = time.time()
        gpu_guard.cooldown(self.gpu, self.max_temp, self.resume_temp)
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel(
            paths.MODEL_NAME,
            use_fp16=True,
            normalize_embeddings=True,
            devices="cuda:0" if self.device == "cuda" else "cpu",
        )
        self._log(f"[load] {paths.MODEL_NAME} ({time.time() - t:.1f}초)")

    # ---------- 하드필터 ----------

    def resolve_company(self, needle: str) -> list[str]:
        """이름 일부 또는 6자리 종목코드로 종목코드 목록을 찾는다."""
        if needle.isdigit() and len(needle) == 6:
            return [needle]
        return sorted({c for nm, c in self.name_to_code.items() if needle in nm})

    def detect_companies(self, query: str) -> list[str]:
        """질의문에 등장하는 회사명을 찾는다.

        인덱스에 든 회사는 70개짜리 닫힌 집합이라 부분 문자열 매칭으로 충분하다.
        긴 이름부터 훑어서 이미 잡힌 구간은 건너뛴다 (짧은 이름이 긴 이름 안에서
        중복으로 걸리는 것을 막는다). 결과는 질의에 나온 순서대로 돌려준다.
        """
        found = []
        spans: list[tuple[int, int]] = []
        for nm in sorted(set(self.name_to_code), key=len, reverse=True):
            i = query.find(nm)
            if i < 0:
                continue
            j = i + len(nm)
            if any(i < e and s_ < j for s_, e in spans):
                continue
            spans.append((i, j))
            found.append((i, nm))
        return [nm for _, nm in sorted(found)]

    def _mask(self, company=None, doc_group=None, year=None):
        mask = None
        if self.latest_only:
            mask = ~self.store.superseded
        if company:
            codes = self.resolve_company(company)
            if not codes:
                raise ValueError(f"회사를 찾지 못했다: {company}")
            mask = np.isin(self.corp_code, codes)
        if doc_group:
            groups = doc_group if isinstance(doc_group, (list, tuple)) else [doc_group]
            m = np.isin(self.doc_group, list(groups))
            mask = m if mask is None else (mask & m)
        if year:
            years = year if isinstance(year, (list, tuple)) else [year]
            m = np.isin(self.base_year, [int(y) for y in years])
            mask = m if mask is None else (mask & m)
        return mask

    # ---------- 검색 ----------

    def encode_query(self, query: str):
        gpu_guard.cooldown(self.gpu, self.max_temp, self.resume_temp)
        out = self.model.encode(
            [query],
            batch_size=1,
            max_length=512,
            return_dense=True,
            return_sparse=self.use_sparse,
            return_colbert_vecs=False,
        )
        q_dense = np.asarray(out["dense_vecs"][0], dtype=np.float32)
        q_sparse = None
        if self.use_sparse:
            q_sparse = np.zeros(paths.SPARSE_VOCAB, dtype=np.float32)
            for tid, w in out["lexical_weights"][0].items():
                q_sparse[int(tid)] = float(w)
        return q_dense, q_sparse

    def score_components(self, query: str):
        """질의 하나에 대한 전체 행 dense/sparse 점수. 인코딩·행렬곱은 여기서 한 번만 한다.

        가중치를 바꿔가며 비교할 때는 이걸 한 번 받아두고 rank()만 다시 부르면 된다.
        반환: (dense_scores, sparse_scores, encode_ms)
        """
        t = time.time()
        q_dense, q_sparse = self.encode_query(query)
        encode_ms = (time.time() - t) * 1000

        if self.device == "cuda":
            import torch

            q = torch.from_numpy(q_dense).to("cuda", torch.float16)
            dense_scores = (self.dense @ q).float().cpu().numpy()
        else:
            dense_scores = self.dense @ q_dense

        if q_sparse is not None:
            sparse_scores = np.asarray(self.sparse @ q_sparse, dtype=np.float32)
        else:
            sparse_scores = np.zeros(self.n, dtype=np.float32)
        return dense_scores, sparse_scores, encode_ms

    def rank(
        self,
        dense_scores,
        sparse_scores,
        k: int,
        w_dense: float = 1.0,
        w_sparse: float = 1.0,
        company: str | None = None,
        doc_group=None,
        year=None,
        max_per_doc: int | None = None,
    ):
        """가중합 + 하드필터 후 상위 k행. (top_rows, 결합점수 전체, 후보 수)를 돌려준다.

        max_per_doc를 주면 한 공시 문서에서 뽑는 청크 수를 제한한다. 같은 섹션이
        여러 청크로 쪼개져 상위를 도배하는 것을 막으려는 것이다 (비교 질의에서 특히 심하다).
        """
        scores = w_dense * dense_scores + w_sparse * sparse_scores

        mask = self._mask(company, doc_group, year)
        n_cand = self.n
        if mask is not None:
            n_cand = int(mask.sum())
            if n_cand == 0:
                raise ValueError("필터를 만족하는 청크가 없다")
            scores = np.where(mask, scores, -np.inf)

        if max_per_doc is None:
            kk = min(k, n_cand)
            top = np.argpartition(-scores, kk - 1)[:kk]
            return top[np.argsort(-scores[top])], scores, n_cand

        # 넉넉히 뽑아놓고 문서별로 세어가며 채운다. 모자라면 배수를 키워 다시 훑는다
        pool = min(max(k * 8, 64), n_cand)
        while True:
            cand = np.argpartition(-scores, pool - 1)[:pool]
            cand = cand[np.argsort(-scores[cand])]
            seen: dict[str, int] = {}
            picked = []
            for row in cand.tolist():
                if not np.isfinite(scores[row]):
                    break
                d = self.doc_id[row]
                if seen.get(d, 0) >= max_per_doc:
                    continue
                seen[d] = seen.get(d, 0) + 1
                picked.append(row)
                if len(picked) == k:
                    break
            if len(picked) == k or pool >= n_cand:
                return np.asarray(picked, dtype=np.int64), scores, n_cand
            pool = min(pool * 4, n_cand)

    def hydrate(self, top, scores, dense_scores, sparse_scores, snippet: int = 400) -> list[Hit]:
        """상위 행만 원본 JSONL에서 읽어 Hit로 채운다."""
        hits = []
        for rank, row in enumerate(top.tolist(), 1):
            rec = self.store.get(row)
            body = rec.get("content", "")
            hits.append(
                Hit(
                    rank=rank,
                    row=row,
                    score=float(scores[row]),
                    dense=float(dense_scores[row]),
                    sparse=float(sparse_scores[row]),
                    chunk_id=rec["chunk_id"],
                    company=rec.get("company", ""),
                    corp_code=rec.get("stock_code", ""),
                    doc_group=rec.get("disclosure_type", ""),
                    receipt_no=rec.get("receipt_no", ""),
                    document_title=rec.get("document_title", ""),
                    section_path=_join_section(rec.get("section_path")),
                    content=body if snippet <= 0 else body[:snippet],
                )
            )
        return hits

    def search(
        self,
        query: str,
        k: int = 10,
        w_dense: float = 1.0,
        w_sparse: float = 1.0,
        company: str | None = None,
        doc_group=None,
        year=None,
        snippet: int = 400,
    ) -> SearchResult:
        t0 = time.time()
        dense_scores, sparse_scores, encode_ms = self.score_components(query)
        score_ms = (time.time() - t0) * 1000 - encode_ms

        t1 = time.time()
        top, scores, n_cand = self.rank(
            dense_scores, sparse_scores, k, w_dense, w_sparse, company, doc_group, year
        )
        search_ms = score_ms + (time.time() - t1) * 1000

        return SearchResult(
            query=query,
            hits=self.hydrate(top, scores, dense_scores, sparse_scores, snippet),
            encode_ms=encode_ms,
            search_ms=search_ms,
            n_candidates=n_cand,
            filters={
                "company": company,
                "doc_group": doc_group,
                "year": year,
                "w_dense": w_dense,
                "w_sparse": w_sparse,
            },
        )

    @staticmethod
    def subquery(query: str, keep: str, entities: list[str]) -> str:
        """비교 질의에서 다른 회사 이름을 지워 개체 하나짜리 부분질의를 만든다.

        "삼성전자와 SK하이닉스의 매출액 비교" -> (삼성전자용) "삼성전자 매출액 비교"

        지우는 회사 이름에 붙은 조사까지 함께 떼야 "삼성전자, 의 배당" 같은 찌꺼기가 안 남는다.
        남기는 회사 뒤의 "와/과"도 떼서 문장을 정리한다.
        """
        out = query
        for other in entities:
            if other == keep:
                continue
            out = re.sub(
                rf"{re.escape(other)}(와|과|의|은|는|이|가|도|및)?\s*,?\s*", "", out, count=1
            )
        # 남기는 회사 뒤에 남은 "와/과/," 도 뗀다
        out = re.sub(rf"({re.escape(keep)})\s*(와|과|,)\s*", r"\g<1> ", out)
        return " ".join(out.split()) or query

    def search_multi(
        self,
        query: str,
        k: int = 6,
        w_dense: float = 1.0,
        w_sparse: float = 1.0,
        doc_group=None,
        year=None,
        snippet: int = 400,
        entities: list[str] | None = None,
        max_per_doc: int | None = 1,
        rewrite: bool = True,
    ) -> SearchResult:
        """비교 질의를 개체별로 쪼개 균등하게 근거를 모은다.

        "삼성전자와 SK하이닉스의 매출액을 비교해줘" 같은 질문은 질의 벡터가 두 개체의
        혼합이라 승자독식으로 한쪽에 전부 쏠린다 (실측: 상위 20건이 전부 SK하이닉스).
        그대로 생성기에 넘기면 한쪽 숫자만 보고 비교를 지어낸다.

        그래서 회사마다 하드필터를 걸고 따로 뽑아 k를 나눠 갖는다.
        회사가 2곳 미만이면 그냥 search()와 같게 동작한다.

        rewrite=True면 회사마다 다른 회사 이름을 지운 부분질의를 새로 인코딩한다.
        같은 혼합 벡터를 회사별 필터로만 나눠 쓰면 근거가 비대칭해진다 — 한쪽은 「매출액」
        주석을 물어오는데 다른 쪽은 종속기업 요약표를 물어오는 식이다. 부분질의는 개체가
        하나뿐이라 단일 질의와 같은 품질이 나온다. 대가는 인코딩 N회(개체당 약 300ms)다.
        """
        ents = entities if entities is not None else self.detect_companies(query)

        t0 = time.time()
        dense_scores, sparse_scores, encode_ms = self.score_components(query)
        score_ms = (time.time() - t0) * 1000 - encode_ms

        t1 = time.time()
        if len(ents) < 2:
            top, scores, n_cand = self.rank(
                dense_scores, sparse_scores, k, w_dense, w_sparse,
                ents[0] if ents else None, doc_group, year, max_per_doc,
            )
            hits = self.hydrate(top, scores, dense_scores, sparse_scores, snippet)
        else:
            per = -(-k // len(ents))  # 올림. 개체마다 최소 한 건은 확보한다
            groups, n_cand = [], 0
            for nm in ents:
                d_s, sp_s = dense_scores, sparse_scores
                if rewrite:
                    sub = self.subquery(query, nm, ents)
                    if sub != query:
                        d_s, sp_s, ms = self.score_components(sub)
                        encode_ms += ms
                try:
                    top, scores, n = self.rank(
                        d_s, sp_s, per, w_dense, w_sparse,
                        nm, doc_group, year, max_per_doc,
                    )
                except ValueError:
                    continue
                n_cand += n
                groups.append(self.hydrate(top, scores, d_s, sp_s, snippet))

            # 회사끼리 번갈아 싣는다. 상위가 한 회사로 채워지지 않게 하려는 것이다
            hits = []
            for i in range(per):
                for g in groups:
                    if i < len(g):
                        hits.append(g[i])
            hits = hits[:k]
            for rank, h in enumerate(hits, 1):
                h.rank = rank

        search_ms = score_ms + (time.time() - t1) * 1000
        return SearchResult(
            query=query,
            hits=hits,
            encode_ms=encode_ms,
            search_ms=search_ms,
            n_candidates=n_cand,
            filters={"company": None, "doc_group": doc_group, "year": year,
                     "w_dense": w_dense, "w_sparse": w_sparse},
            entities=ents,
        )

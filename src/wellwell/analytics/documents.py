import math
import re
from collections import Counter
from functools import lru_cache

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from wellwell.analytics.result import ToolResult

_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(value.lower())


class _Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.docs = [Counter(_tokens(f"{c['title']} {c['section']} {c['text']}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / max(len(self.lengths), 1)
        frequency: Counter = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        total = len(self.docs)
        self.idf = {term: math.log(1 + (total - n + 0.5) / (n + 0.5)) for term, n in frequency.items()}

    def search(self, query: str, k: int, allowed: set[int] | None = None) -> list[tuple[float, dict]]:
        terms = _tokens(query)
        scored = []
        for index, doc in enumerate(self.docs):
            if allowed is not None and index not in allowed:
                continue
            score = 0.0
            for term in terms:
                tf = doc.get(term)
                if tf:
                    score += self.idf.get(term, 0) * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * self.lengths[index] / self.avg))
            if score > 0:
                scored.append((score, self.chunks[index]))
        scored.sort(key=lambda item: -item[0])
        return scored[:k]


@lru_cache(maxsize=1)
def _index(version: int) -> _Index:
    from wellwell.db.session import session_scope

    with session_scope() as session:
        rows = session.execute(text(
            """SELECT c.id, c.doc_id, c.section, c.text, d.title, d.kind, d.asset_id, d.well_type, d.maintenance_id
                 FROM document_chunks c JOIN documents d ON d.doc_id = c.doc_id ORDER BY c.doc_id, c.chunk_index"""
        )).mappings().all()
    return _Index([dict(r) for r in rows])


SEARCH_MODES = ("hybrid", "semantic", "keyword")


def search_documents(session: Session, query: str, kind: str | None = None, well: str | None = None,
                     k: int = 5, mode: str = "semantic") -> ToolResult:
    """Hybrid search: BM25 for exact terms (SR-02, §10) fused with BGE vectors for meaning, by reciprocal rank."""
    from wellwell.analytics import embeddings

    version = session.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar()
    index = _index(version)
    allowed = None
    if kind or well:
        allowed = {i for i, c in enumerate(index.chunks)
                   if (not kind or c["kind"] == kind) and (not well or c["asset_id"] == well)}
    pool = max(k * 6, 30)
    mode = mode if mode in SEARCH_MODES else "semantic"
    if mode == "semantic" and not embeddings.available():
        mode = "keyword"
    fused: dict[int, float] = {}
    by_id = {c["id"]: c for c in index.chunks}
    if mode in ("hybrid", "keyword"):
        for rank, (_, chunk) in enumerate(index.search(query, pool, allowed)):
            fused[chunk["id"]] = fused.get(chunk["id"], 0) + 1 / (60 + rank)
    method = "keyword (BM25)"
    if mode in ("hybrid", "semantic") and embeddings.available():
        try:
            allowed_ids = {index.chunks[i]["id"] for i in allowed} if allowed is not None else None
            for rank, (chunk_id, _) in enumerate(embeddings.semantic_ranking(query, allowed_ids, pool)):
                fused[chunk_id] = fused.get(chunk_id, 0) + 1 / (60 + rank)
            method = "hybrid (BM25 + BGE-large)" if mode == "hybrid" else "semantic (BGE-large)"
        except Exception as error:
            structlog.get_logger().warning("search.semantic_failed", error=str(error)[:200])
            if mode == "semantic":
                for rank, (_, chunk) in enumerate(index.search(query, pool, allowed)):
                    fused[chunk["id"]] = fused.get(chunk["id"], 0) + 1 / (60 + rank)
                method = "keyword (BM25), because semantic search failed"
    ranked = sorted(fused.items(), key=lambda item: -item[1])[:k]
    rows = [{"doc_id": by_id[i]["doc_id"], "title": by_id[i]["title"], "section": by_id[i]["section"],
             "kind": by_id[i]["kind"], "well": by_id[i]["asset_id"], "score": round(score, 4),
             "text": by_id[i]["text"][:1500]} for i, score in ranked if i in by_id]
    summary = (f"{len(rows)} passage(s) for '{query}' via {method}: "
               + "; ".join(f"{r['doc_id']} › {r['section']}" for r in rows))
    return ToolResult(summary=summary, rows=rows, citations=[f"{r['doc_id']} › {r['section']}" for r in rows])

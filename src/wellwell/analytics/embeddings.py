"""Dense vectors for manual, SOP and report sections, cached in the database by text hash.

The cache lives outside the app's tables (its own Postgres schema), so resets and reloads never
throw away work: a section is embedded once per model, ever.
"""

import hashlib
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import numpy as np
import structlog
from sqlalchemy import text

from wellwell.config import PROJECT_ROOT, get_settings
from wellwell.db.dialect import is_postgres
from wellwell.db.session import get_engine

log = structlog.get_logger()

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _cache_table() -> str:
    return "vector_cache.chunk_vectors" if is_postgres() else "chunk_vectors_cache"


def ensure_cache() -> None:
    with get_engine().begin() as connection:
        if is_postgres():
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS vector_cache"))
        connection.execute(text(
            f"""CREATE TABLE IF NOT EXISTS {_cache_table()} (
                    text_hash VARCHAR(64) NOT NULL,
                    model VARCHAR(120) NOT NULL,
                    dim INTEGER NOT NULL,
                    vector {'BYTEA' if is_postgres() else 'BLOB'} NOT NULL,
                    PRIMARY KEY (text_hash, model))"""))


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def section_text(title: str | None, section: str | None, body: str) -> str:
    return f"{title or ''} — {section or ''}\n{body}".strip()


MODEL_STATE = {"state": "not_loaded", "error": None}


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    import torch

    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    name = get_settings().embed_model
    log.info("embeddings.loading_model", model=name, device=device)
    MODEL_STATE.update(state="loading", device=device)
    try:
        model = SentenceTransformer(name, device=device)
    except Exception as error:
        MODEL_STATE.update(state="failed", error=str(error)[:300])
        raise
    MODEL_STATE.update(state="loaded")
    model.max_seq_length = 256
    if device in ("mps", "cuda"):
        model.half()
    return model


def available() -> bool:
    if get_settings().embed_provider != "bge":
        return False
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def embed_texts(values: list[str], batch_size: int = 64) -> np.ndarray:
    vectors = _model().encode(values, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False,
                              convert_to_numpy=True)
    return np.asarray(vectors, dtype=np.float32)


def embed_query(question: str) -> np.ndarray:
    return embed_texts([QUERY_PREFIX + question])[0]


def index_documents(progress: Callable[..., None] | None = None, batch: int = 128, shard: int = 0,
                    shards: int = 1) -> dict:
    """Embeds every section whose text isn't cached yet. Returns counts for the load report."""
    if not available():
        return {"skipped": "embeddings are off (EMBED_PROVIDER is not 'bge' or the extra isn't installed)"}
    ensure_cache()
    seeded = import_seed()
    if seeded and progress:
        progress(stage=f"Imported {seeded} stored search vectors")
    model_name = get_settings().embed_model
    with get_engine().connect() as connection:
        rows = connection.execute(text(
            "SELECT c.id, c.section, c.text, d.title FROM document_chunks c JOIN documents d ON d.doc_id = c.doc_id"
        )).mappings().all()
        cached = set(connection.execute(text(f"SELECT text_hash FROM {_cache_table()} WHERE model = :m"),
                                        {"m": model_name}).scalars())
    pending: dict[str, str] = {}
    for row in rows:
        body = section_text(row["title"], row["section"], row["text"])
        digest = text_hash(body)
        if digest not in cached and int(digest[:8], 16) % shards == shard:
            pending[digest] = body
    total = len(pending)
    done = 0
    items = list(pending.items())
    for start in range(0, total, batch):
        chunk = items[start:start + batch]
        vectors = embed_texts([body for _, body in chunk])
        with get_engine().begin() as connection:
            connection.execute(
                text(f"INSERT INTO {_cache_table()} (text_hash, model, dim, vector) VALUES (:h, :m, :d, :v) "
                     "ON CONFLICT (text_hash, model) DO NOTHING"),
                [{"h": digest, "m": model_name, "d": int(vector.shape[0]), "v": vector.astype(np.float32).tobytes()}
                 for (digest, _), vector in zip(chunk, vectors, strict=True)])
        done += len(chunk)
        if progress:
            progress(stage=f"Indexing documents for search: {done} of {total} new sections")
    _matrix.cache_clear()
    return {"sections": len(rows), "newly_embedded": total, "reused_from_cache": len(rows) - total}


@lru_cache(maxsize=1)
def _matrix() -> tuple[list[int], np.ndarray]:
    ensure_cache()
    model_name = get_settings().embed_model
    with get_engine().connect() as connection:
        rows = connection.execute(text(
            "SELECT c.id, c.section, c.text, d.title FROM document_chunks c JOIN documents d ON d.doc_id = c.doc_id"
        )).mappings().all()
        vectors = dict(connection.execute(
            text(f"SELECT text_hash, vector FROM {_cache_table()} WHERE model = :m"), {"m": model_name}).all())
    ids, matrix = [], []
    for row in rows:
        blob = vectors.get(text_hash(section_text(row["title"], row["section"], row["text"])))
        if blob is not None:
            ids.append(row["id"])
            matrix.append(np.frombuffer(bytes(blob), dtype=np.float32))
    return ids, (np.vstack(matrix) if matrix else np.zeros((0, 1024), dtype=np.float32))


def semantic_ranking(question: str, allowed_ids: set[int] | None, k: int) -> list[tuple[int, float]]:
    """Chunk ids ranked by cosine similarity to the question (vectors are normalised, so a dot product)."""
    ids, matrix = _matrix()
    if not ids:
        return []
    scores = matrix @ embed_query(question)
    order = np.argsort(-scores)
    ranked = []
    for index in order:
        chunk_id = ids[index]
        if allowed_ids is None or chunk_id in allowed_ids:
            ranked.append((chunk_id, float(scores[index])))
            if len(ranked) >= k:
                break
    return ranked


SEED_DIR = PROJECT_ROOT / "seed"


def seed_path() -> Path:
    return SEED_DIR / f"vectors-{get_settings().embed_model.split('/')[-1]}.npz"


def export_seed() -> dict:
    """Writes every cached vector for the current model to a compact file that ships with the repo."""
    ensure_cache()
    with get_engine().connect() as connection:
        rows = connection.execute(text(f"SELECT text_hash, vector FROM {_cache_table()} WHERE model = :m"),
                                  {"m": get_settings().embed_model}).all()
    hashes = np.array([r[0] for r in rows], dtype="S64")
    vectors = np.vstack([np.frombuffer(bytes(r[1]), dtype=np.float32) for r in rows]).astype(np.float16)
    SEED_DIR.mkdir(exist_ok=True)
    np.savez_compressed(seed_path(), hashes=hashes, vectors=vectors, model=np.array(get_settings().embed_model))
    return {"vectors": len(rows), "file": str(seed_path()), "mb": round(seed_path().stat().st_size / 1e6, 1)}


def import_seed() -> int:
    """Loads shipped vectors into the cache so a fresh install doesn't spend an hour embedding on CPU."""
    path = seed_path()
    if not path.exists():
        return 0
    ensure_cache()
    data = np.load(path)
    if str(data["model"]) != get_settings().embed_model:
        return 0
    model_name = get_settings().embed_model
    with get_engine().connect() as connection:
        existing = set(connection.execute(text(f"SELECT text_hash FROM {_cache_table()} WHERE model = :m"),
                                          {"m": model_name}).scalars())
    rows = [{"h": h.decode(), "m": model_name, "d": int(v.shape[0]), "v": v.astype(np.float32).tobytes()}
            for h, v in zip(data["hashes"], data["vectors"], strict=True) if h.decode() not in existing]
    for start in range(0, len(rows), 2000):
        with get_engine().begin() as connection:
            connection.execute(text(f"INSERT INTO {_cache_table()} (text_hash, model, dim, vector) "
                                    "VALUES (:h, :m, :d, :v) ON CONFLICT (text_hash, model) DO NOTHING"),
                               rows[start:start + 2000])
    _matrix.cache_clear()
    return len(rows)


def index_status() -> dict:
    """How much of the search index exists, for the loading dialog."""
    if not available():
        return {"enabled": False, "sections": 0, "vectors": 0, "model": MODEL_STATE}
    ensure_cache()
    with get_engine().connect() as connection:
        sections = connection.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar() or 0
        vectors = connection.execute(text(f"SELECT COUNT(*) FROM {_cache_table()} WHERE model = :m"),
                                     {"m": get_settings().embed_model}).scalar() or 0
    return {"enabled": True, "sections": sections, "vectors": min(vectors, sections) if sections else vectors,
            "embedding_model": get_settings().embed_model, "model": MODEL_STATE}

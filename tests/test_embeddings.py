import numpy as np
import pytest
from sqlalchemy import text

from wellwell.analytics import embeddings
from wellwell.analytics.documents import search_documents
from wellwell.db.dialect import is_postgres
from wellwell.db.session import get_engine, session_scope

pytestmark = pytest.mark.skipif(not embeddings.available(), reason="embeddings extra not installed or turned off")


def _cached_count() -> int:
    with get_engine().connect() as connection:
        return connection.execute(text(f"SELECT COUNT(*) FROM {embeddings._cache_table()}")).scalar()


def test_every_section_has_a_stored_vector():
    with get_engine().connect() as connection:
        sections = connection.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar()
    ids, matrix = embeddings._matrix()
    assert len(ids) == sections and matrix.shape == (sections, 1024)


def test_vectors_are_normalised_float32():
    _, matrix = embeddings._matrix()
    norms = np.linalg.norm(matrix[:200], axis=1)
    assert matrix.dtype == np.float32 and np.allclose(norms, 1.0, atol=1e-2)


def test_second_index_run_reuses_everything():
    before = _cached_count()
    result = embeddings.index_documents()
    assert result["newly_embedded"] == 0 and result["reused_from_cache"] == result["sections"]
    assert _cached_count() == before


def test_only_new_text_is_embedded(monkeypatch):
    calls = []

    def fake_embed(values, batch_size=64):
        calls.append(len(values))
        return np.ones((len(values), 1024), dtype=np.float32) / 32

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    body = "Temporary test section about an unusual gearbox whistle on a pump jack."
    with get_engine().begin() as connection:
        doc_id = connection.execute(text("SELECT doc_id FROM documents LIMIT 1")).scalar()
        connection.execute(text("INSERT INTO document_chunks (doc_id, chunk_index, section, text) "
                                "VALUES (:d, 9999, 'Test', :t)"), {"d": doc_id, "t": body})
    try:
        result = embeddings.index_documents()
        assert result["newly_embedded"] == 1 and calls == [1]
        assert embeddings.index_documents()["newly_embedded"] == 0
    finally:
        with get_engine().begin() as connection:
            title = connection.execute(text("SELECT title FROM documents WHERE doc_id = :d"), {"d": doc_id}).scalar()
            connection.execute(text("DELETE FROM document_chunks WHERE chunk_index = 9999 AND doc_id = :d"),
                               {"d": doc_id})
            connection.execute(text(f"DELETE FROM {embeddings._cache_table()} WHERE text_hash = :h"),
                               {"h": embeddings.text_hash(embeddings.section_text(title, "Test", body))})
        embeddings._matrix.cache_clear()


def test_shards_cover_every_section_exactly_once():
    hashes = [embeddings.text_hash(f"section {i}") for i in range(2000)]
    for shards in (2, 3, 4):
        buckets = [[h for h in hashes if int(h[:8], 16) % shards == s] for s in range(shards)]
        assert sorted(h for b in buckets for h in b) == sorted(hashes)
        assert all(b for b in buckets)


def test_hash_is_stable_and_content_based():
    a = embeddings.section_text("T200 manual", "9.3", "at least 15 °C above ambient")
    assert embeddings.text_hash(a) == embeddings.text_hash(a)
    assert embeddings.text_hash(a) != embeddings.text_hash(a + " ")


@pytest.mark.parametrize("question, kind, expected_doc, expected_section", [
    ("when does a well count as producing oil", "manual", "OM-T200", "9.3"),
    ("how quickly must a spill be contained and reported", "sop", "SOP-SR", "Protocols"),
    ("how often must every well be inspected", "sop", "SOP-RC", "Protocols"),
    ("what must the crew do before welding near the wellhead", "sop", "SOP-WS", "Protocols"),
])
def test_semantic_search_finds_the_rule(question, kind, expected_doc, expected_section):
    with session_scope() as session:
        rows = search_documents(session, question, kind=kind, k=3).rows
    assert any(r["doc_id"] == expected_doc and expected_section in (r["section"] or "") for r in rows)


def test_exact_protocol_ids_still_found_by_keyword_half():
    with session_scope() as session:
        rows = search_documents(session, "SR-02", kind="sop", k=3, mode="hybrid").rows
    assert rows and rows[0]["doc_id"] == "SOP-SR"


def test_filters_are_respected():
    with session_scope() as session:
        rows = search_documents(session, "flowline leak and clean-up", kind="maintenance_report", well="WELL-0001",
                                k=5).rows
    assert rows and all(r["kind"] == "maintenance_report" and r["well"] == "WELL-0001" for r in rows)


def test_search_reports_hybrid_and_falls_back_to_keywords(monkeypatch):
    with session_scope() as session:
        assert "semantic" in search_documents(session, "containment time", k=2).summary
        assert "hybrid" in search_documents(session, "containment time", k=2, mode="hybrid").summary
        monkeypatch.setattr(embeddings, "available", lambda: False)
        assert "keyword (BM25)" in search_documents(session, "containment time", k=2).summary


def test_semantic_failure_degrades_to_keywords(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("model crashed")

    monkeypatch.setattr(embeddings, "semantic_ranking", broken)
    with session_scope() as session:
        result = search_documents(session, "containment time", kind="sop", k=2)
    assert result.rows and "keyword (BM25)" in result.summary


def test_disabled_indexing_is_a_clear_no_op(monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: False)
    assert "skipped" in embeddings.index_documents()


@pytest.mark.skipif(not is_postgres(), reason="schema separation is a Postgres feature")
def test_vector_cache_lives_outside_the_reset_schema():
    with get_engine().connect() as connection:
        schema = connection.execute(text(
            "SELECT table_schema FROM information_schema.tables WHERE table_name = 'chunk_vectors'")).scalar()
    assert schema == "vector_cache"


@pytest.mark.parametrize("mode, label", [("hybrid", "hybrid"), ("semantic", "semantic (BGE-large)"),
                                         ("keyword", "keyword (BM25)")])
def test_search_mode_toggle(mode, label):
    with session_scope() as session:
        result = search_documents(session, "when does a well count as producing oil", kind="manual", k=3, mode=mode)
    assert label in result.summary and result.rows

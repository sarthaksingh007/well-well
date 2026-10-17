from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

from wellwell.db.models import LoadRun
from wellwell.db.session import create_schema, create_views, get_engine, session_scope
from wellwell.ingestion.loader import DatasetLoader, LoadReport


def _update(run_id: int, **fields) -> None:
    """Progress is written in its own short transaction so other connections can see it while loading."""
    with session_scope() as session:
        run = session.get(LoadRun, run_id)
        if run is not None:
            for key, value in fields.items():
                setattr(run, key, value)


def load_dataset(path: Path, dataset: str, reset: bool = False, echo=print) -> LoadReport:
    create_schema(drop_first=reset)
    with session_scope() as session:
        run = LoadRun(dataset=dataset, path=str(path), status="running", stage="Starting")
        session.add(run)
        session.flush()
        run_id = run.id

    def progress(**fields) -> None:
        _update(run_id, **fields)
        if "stage" in fields:
            echo(f"  {fields['stage']}")

    try:
        with session_scope() as session:
            report = DatasetLoader(session, path, dataset, progress).run()
        create_views()
    except Exception as error:
        _update(run_id, status="failed", error=str(error)[:2000], finished_at=datetime.now())
        raise
    try:
        from wellwell.analytics.embeddings import index_documents

        report.counts["search_index"] = index_documents(progress=progress)
    except Exception as error:
        report.warnings.append(f"Semantic search index not built, keyword search still works: {str(error)[:300]}")
    _update(run_id, status="done", stage="Done", counts=report.counts, warnings=report.warnings[:200],
            finished_at=datetime.now())
    return report


def status() -> dict:
    result = _status()
    result["steps"] = _steps(result)
    return result


def _steps(result: dict) -> list[dict]:
    """The checklist the loading dialog shows. Only the first two block questions."""
    from wellwell.analytics import embeddings

    state = result["state"]
    stats = result.get("stats") or {}
    run = result.get("run") or {}
    steps = [{"key": "database", "label": "Database connected", "blocking": True,
              "status": "pending" if state == "initialising" else "done"}]
    if state in ("loading", "updating"):
        dataset = {"status": "running", "detail": result["message"], "percent": result.get("percent")}
    elif state == "failed":
        dataset = {"status": "failed", "detail": result["message"]}
    elif stats.get("wells"):
        dataset = {"status": "done", "detail": f"{stats['wells']} wells · {stats['inspections']:,} inspections · "
                                                f"{stats['repairs']:,} repairs · {stats['repair_reports']:,} reports"}
    else:
        dataset = {"status": "pending", "detail": "Waiting for the dataset"}
    steps.append({"key": "dataset", "label": "Dataset loaded", "blocking": True, **dataset})
    try:
        index = embeddings.index_status() if state not in ("initialising",) else None
    except Exception:
        index = None
    if index and index["enabled"]:
        total, done = index["sections"], index["vectors"]
        indexing = "running" if (run.get("status") == "running"
                                  and (run.get("stage") or "").startswith(("Indexing", "Imported"))) else None
        status_value = "done" if total and done >= total else (indexing or ("pending" if not done else "running"))
        steps.append({"key": "vectors", "label": "Search vectors", "blocking": False, "status": status_value,
                      "detail": f"{done:,} of {total:,} sections ({index['embedding_model'].split('/')[-1]})",
                      "percent": round(100 * done / total) if total else 0})
        model_state = index["model"]["state"]
        steps.append({"key": "model", "label": "Embedding model loaded", "blocking": False,
                      "status": {"loaded": "done", "loading": "running", "failed": "failed"}.get(model_state, "pending"),
                      "detail": index["model"].get("error") or ("Loading BGE-large (first start downloads ~1.3 GB)"
                                                                if model_state == "loading" else "")})
    else:
        steps.append({"key": "vectors", "label": "Search vectors", "blocking": False, "status": "skipped",
                      "detail": "Semantic search is off; keyword search is used"})
    return steps


def _status() -> dict:
    """What the UI needs before letting anyone ask: is there data, is a load running, and what's in it."""
    engine = get_engine()
    tables = set(inspect(engine).get_table_names())
    if "wells" not in tables or "load_runs" not in tables:
        return {"state": "initialising", "message": "Setting up the database…", "stats": None, "run": None}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        run = connection.execute(text(
            "SELECT id, dataset, status, stage, wells_total, wells_done, warnings, error, started_at, finished_at "
            "FROM load_runs ORDER BY id DESC LIMIT 1")).mappings().first()
        wells = connection.execute(text("SELECT COUNT(*) FROM wells")).scalar() or 0
        stats = None
        views = set(inspect(engine).get_view_names())
        if wells:
            row = connection.execute(text("""
                SELECT (SELECT COUNT(*) FROM wells) AS wells, (SELECT COUNT(*) FROM areas) AS areas,
                       (SELECT COUNT(*) FROM users) AS users, (SELECT COUNT(*) FROM inspections) AS inspections,
                       (SELECT COUNT(*) FROM maintenance) AS repairs,
                       (SELECT COUNT(*) FROM documents WHERE kind = 'maintenance_report') AS repair_reports,
                       (SELECT COUNT(*) FROM documents WHERE kind IN ('manual', 'sop')) AS manuals_and_sops,
                       (SELECT COUNT(*) FROM regulatory_events) AS regulator_events,
                       (SELECT COUNT(*) FROM notes WHERE origin = 'dataset') AS notes,
                       (SELECT COUNT(*) FROM corrections WHERE source_note_id IS NOT NULL) AS dataset_corrections,
                       (SELECT MIN(inspection_date) FROM inspections) AS first_inspection,
                       (SELECT MAX(inspection_date) FROM inspections) AS last_inspection""")).mappings().first()
            stats = dict(row)
            stats["datasets"] = [dict(r) for r in connection.execute(text(
                "SELECT dataset, COUNT(*) AS wells FROM wells GROUP BY dataset ORDER BY dataset")).mappings()]
            stats["memory"] = dict(connection.execute(text("""
                SELECT (SELECT COUNT(*) FROM corrections WHERE source_note_id IS NULL) AS corrections,
                       (SELECT COUNT(*) FROM notes WHERE origin = 'copilot') AS shared_notes,
                       (SELECT COUNT(*) FROM claims) AS claims,
                       (SELECT COUNT(*) FROM rule_overrides WHERE status = 'active') AS team_rules,
                       (SELECT COUNT(*) FROM conversations) AS conversations""")).mappings().first())
    run_info = dict(run) if run else None
    if run_info and run_info["status"] == "running" and run_info["dataset"] != "search index":
        total = run_info["wells_total"] or 0
        percent = round(100 * (run_info["wells_done"] or 0) / total) if total else 0
        state = "loading" if not wells or "inspection_facts" not in views else "updating"
        message = (f"Loading the {run_info['dataset']} dataset: {run_info['wells_done']} of {total} wells "
                   f"({percent}%). {run_info['stage'] or ''}")
        if state == "updating":
            message += " Answers use the data already loaded until this finishes."
        return {"state": state, "message": message, "percent": percent, "stats": stats, "run": run_info}
    if wells and "inspection_facts" not in views:
        return {"state": "failed", "stats": stats, "run": run_info,
                "message": "The data is loaded but the database is incomplete (views missing). Run `wellwell init` again."}
    if not wells:
        failed = run_info and run_info["status"] == "failed"
        return {"state": "failed" if failed else "empty",
                "message": (f"The last load failed: {run_info['error']}" if failed
                            else "No data loaded yet. Run `wellwell init` (Docker does this on start)."),
                "stats": stats, "run": run_info}
    return {"state": "ready", "message": "Ready", "stats": stats, "run": run_info}

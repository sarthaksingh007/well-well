from pathlib import Path

import typer

from wellwell.config import PROJECT_ROOT

app = typer.Typer(help="Well-Well command line")

DEFAULT_SAMPLE = PROJECT_ROOT / "data" / "raw" / "challenge" / "sample-data"


@app.callback()
def main() -> None:
    pass


@app.command()
def load(path: Path = typer.Argument(DEFAULT_SAMPLE), dataset: str = "sample", reset: bool = False) -> None:
    """Load a dataset folder. Run again with --dataset test on the test release to add it alongside."""
    from wellwell.services.loading import load_dataset

    report = load_dataset(path, dataset, reset=reset, echo=typer.echo)
    for table, count in sorted(report.counts.items()):
        typer.echo(f"{table:>20}: {count}")
    for warning in report.warnings:
        typer.secho(f"warning: {warning}", fg="yellow")


DATASET_REPO = "https://github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset"


@app.command("load-github")
def load_github(folder: str = "test-data/dataset", dataset: str = "test", reset: bool = True,
                repo: str = DATASET_REPO) -> None:
    """Fetch the latest challenge dataset from GitHub and load one folder of it (the test release by default)."""
    import subprocess

    checkout = PROJECT_ROOT / "data" / "raw" / "challenge"
    if checkout.exists() and not (checkout / ".git").exists():
        checkout = PROJECT_ROOT / "data" / "raw" / "github"
    if (checkout / ".git").exists():
        typer.echo(f"Updating {checkout} from GitHub…")
        subprocess.run(["git", "-C", str(checkout), "pull", "--ff-only", "-q"], check=True)
    else:
        typer.echo(f"Cloning {repo}…")
        checkout.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "-q", repo, str(checkout)], check=True)
    path = checkout / folder
    if not (path / "fleet").exists():
        raise typer.BadParameter(f"{path} has no fleet/ folder; pick the folder that holds fleet/ and assets/")
    load(path, dataset, reset)


@app.command()
def ask(question: str, user: str = "U-201", conversation: int | None = None, provider: str | None = None) -> None:
    """Ask the copilot a question as a user (default Michael Turner, U-201)."""
    import json

    from wellwell.services.copilot import ask as ask_copilot

    reply = ask_copilot(user, question, conversation, provider, on_step=lambda s: typer.secho(f"  → {s}", fg="cyan"))
    typer.echo(f"\n[conversation {reply.conversation_id} · {reply.model} · {reply.latency_ms} ms]\n")
    typer.echo(reply.answer)
    if reply.visual:
        typer.secho(f"\nvisual: {reply.visual['kind']} from step {reply.visual['step']} ({reply.visual['tool']}), "
                    f"{len(reply.visual.get('rows') or [])} rows", fg="green")
    typer.secho(f"citations: {', '.join(reply.citations)}", fg="green")
    typer.secho(f"trust: {json.dumps(reply.trust)[:600]}", fg="yellow")



@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the API and the web app."""
    import uvicorn

    uvicorn.run("wellwell.api.app:app", host=host, port=port)



@app.command()
def init(path: Path = typer.Argument(DEFAULT_SAMPLE)) -> None:
    """Create the schema and load the sample dataset if the database is empty. Safe to run on every start."""
    from sqlalchemy import inspect, text

    from wellwell.db.session import create_schema, create_views, get_engine
    from wellwell.services.loading import load_dataset

    engine = get_engine()
    has_wells = inspect(engine).has_table("wells")
    create_schema()
    with engine.connect() as connection:
        empty = not has_wells or connection.execute(text("SELECT COUNT(*) FROM wells")).scalar() == 0
    if empty:
        typer.echo(f"Loading {path} ...")
        load_dataset(path, "sample", echo=typer.echo)
    create_views()
    typer.echo("Database ready.")



@app.command()
def embed(workers: int = 1, shard: int = -1) -> None:
    """Embed any manual, SOP or report sections not yet in the vector cache (BGE-large).

    --workers N splits the work into N shards and runs them in parallel processes.
    """
    import subprocess
    import sys

    from wellwell.analytics.embeddings import index_documents

    if shard >= 0:
        typer.echo(index_documents(progress=lambda **f: typer.echo(f"  [{shard}] {f.get('stage', '')}"),
                                   shard=shard, shards=workers))
        return
    if workers == 1:
        from datetime import datetime

        from wellwell.db.models import LoadRun
        from wellwell.db.session import session_scope

        with session_scope() as session:
            run = LoadRun(dataset="search index", path="vector_cache", status="running", stage="Indexing documents")
            session.add(run)
            session.flush()
            run_id = run.id

        def progress(**fields):
            typer.echo(f"  {fields.get('stage', '')}")
            with session_scope() as session:
                session.get(LoadRun, run_id).stage = fields.get("stage")

        try:
            result = index_documents(progress=progress)
        finally:
            with session_scope() as session:
                finished = session.get(LoadRun, run_id)
                finished.status, finished.finished_at = "done", datetime.now()
        typer.echo(result)
        return
    processes = [subprocess.Popen([sys.executable, "-m", "wellwell.cli", "embed", "--workers", str(workers),
                                   "--shard", str(index)]) for index in range(workers)]
    codes = [process.wait() for process in processes]
    typer.echo(f"workers finished with exit codes {codes}")



@app.command("export-vectors")
def export_vectors() -> None:
    """Save the stored search vectors to seed/ so fresh installs (and Docker) start with them."""
    from wellwell.analytics.embeddings import export_seed

    typer.echo(export_seed())


if __name__ == "__main__":
    app()

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {
    "wells", "equipment", "areas", "teams", "users", "well_type_rules", "inspections", "maintenance",
    "report_compliance", "regulatory_events", "notes", "corrections", "rule_overrides", "claims",
    "inspection_facts", "latest_inspection", "documents", "document_chunks",
}


def _dialect() -> str:
    from wellwell.db.dialect import is_postgres

    return "postgres" if is_postgres() else "sqlite"


class SQLRejected(ValueError):
    pass


def guard_sql(sql: str, row_limit: int = 200) -> str:
    """Only a single read-only SELECT over the data tables gets through, always with a row limit."""
    try:
        statements = sqlglot.parse(sql, read=_dialect())
    except sqlglot.errors.ParseError as error:
        raise SQLRejected(f"Could not parse SQL: {error}") from error
    if len(statements) != 1 or statements[0] is None:
        raise SQLRejected("Exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.Union | exp.With) and not tree.find(exp.Select):
        raise SQLRejected("Only SELECT queries are allowed")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Pragma)
    if any(tree.find(kind) for kind in forbidden):
        raise SQLRejected("Only read-only queries are allowed")
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name and name not in ALLOWED_TABLES and name not in ctes:
            raise SQLRejected(f"Table '{table.name}' is not available. Allowed: {sorted(ALLOWED_TABLES)}")
    if not tree.args.get("limit"):
        tree = tree.limit(row_limit)
    return tree.sql(dialect=_dialect())

"""Small, body-free projection shared by API and SQL execution records.

No collector, polling worker, database connection or extra dependency is required.
The existing record transaction updates this index; original evidence stays put.
"""

SOURCES = {"ui", "cli", "skill", "api", "unknown"}
COLUMNS = (
    "kind",
    "id",
    "created_at",
    "name",
    "environment",
    "state",
    "duration_ms",
    "status_code",
    "source",
    "agent_name",
    "task_id",
    "mode",
)


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS execution_records (
        kind TEXT NOT NULL, id TEXT NOT NULL, created_at TEXT NOT NULL,
        name TEXT NOT NULL, environment TEXT NOT NULL, state TEXT NOT NULL,
        duration_ms REAL, status_code INTEGER, source TEXT NOT NULL,
        agent_name TEXT NOT NULL, task_id TEXT NOT NULL, mode TEXT NOT NULL,
        PRIMARY KEY(kind,id))""")
    for name, columns in {
        "created": "created_at DESC,kind,id",
        "task": "task_id,created_at DESC",
        "agent": "agent_name,created_at DESC",
        "source": "source,created_at DESC",
    }.items():
        db.execute(
            f"CREATE INDEX IF NOT EXISTS execution_{name} ON execution_records ({columns})"
        )


def index(db, kind, value):
    context = value.get("execution_context") or {}
    source = context.get("source", "unknown")
    source = source if source in SOURCES else "unknown"
    api = kind == "api"
    fields = (
        kind,
        value["id"],
        value["created_at"],
        str(value.get("name", ""))[:120]
        if api
        else str(value.get("connection_name", ""))[:80]
        + " / "
        + str(value.get("database", ""))[:64],
        str(value.get("environment_name" if api else "environment", ""))[:200],
        value["state"],
        value.get("total_ms" if api else "elapsed_ms"),
        value.get("status_code") if api else None,
        source,
        str(context.get("agent_name", ""))[:80],
        str(context.get("task_id", ""))[:128],
        str(value.get("method" if api else "mode", ""))[:16],
    )
    db.execute(
        """INSERT INTO execution_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(kind,id) DO UPDATE SET state=excluded.state,
        duration_ms=excluded.duration_ms,status_code=excluded.status_code,
        source=excluded.source,agent_name=excluded.agent_name,task_id=excluded.task_id""",
        fields,
    )


def recover_sql(db):
    # SQL history is capped at 10,000. Project only metadata inside SQLite;
    # do not decrypt a record or load results to rebuild this derived index.
    db.execute("""INSERT OR IGNORE INTO execution_records
        SELECT 'sql',id,created,
          COALESCE(json_extract(summary,'$.connection_name'),'') || ' / ' || COALESCE(json_extract(summary,'$.database'),''),
          COALESCE(json_extract(summary,'$.environment'),''),json_extract(summary,'$.state'),
          json_extract(summary,'$.elapsed_ms'),NULL,
          COALESCE(json_extract(summary,'$.execution_context.source'),'unknown'),
          COALESCE(json_extract(summary,'$.execution_context.agent_name'),''),
          COALESCE(json_extract(summary,'$.execution_context.task_id'),''),
          COALESCE(json_extract(summary,'$.mode'),'') FROM sql_runs""")
    db.execute("""UPDATE execution_records SET state=CASE
        WHEN state='running' AND mode='create' THEN 'unknown' ELSE 'interrupted' END
        WHERE kind='sql' AND state IN ('running','awaiting_confirmation')""")


def page(store, page=1, limit=20, **filters):
    # Every filter is a parameter. Callers cannot supply column names or ordering.
    clauses, params = [], []
    for key in ("kind", "source", "agent_name", "task_id", "environment", "state"):
        if filters.get(key):
            clauses.append(key + "=?")
            params.append(filters[key])
    search = filters.get("search", "")
    if search:
        term = (
            "%"
            + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        clauses.append("(name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
        params.extend([term, term])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with store.lock:
        count = store.db.execute(
            "SELECT COUNT(*) FROM execution_records" + where, params
        ).fetchone()[0]
        rows = store.db.execute(
            "SELECT "
            + ",".join(COLUMNS)
            + " FROM execution_records"
            + where
            + " ORDER BY created_at DESC,kind DESC,id DESC LIMIT ? OFFSET ?",
            [*params, limit, (page - 1) * limit],
        ).fetchall()
    return {
        "items": [dict(zip(COLUMNS, row)) for row in rows],
        "total": count,
        "page": page,
        "limit": limit,
    }

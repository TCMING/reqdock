import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from api_post.app import create_app
from api_post.storage import Store, now
from test_workbench import client, upstream, environment, submit, finish, MemoryVault
from test_sql_workspace import mysql, profile, start, wait


def test_api_sql_share_task_and_keep_original_histories(client, upstream, mysql):
    env = environment(client, upstream)
    ctx = {"source": "skill", "agent_name": "Codex", "task_id": "task-shared-42"}
    api = finish(client, submit(client, env, execution_context=ctx)["id"])
    p = profile(client, mysql)
    sql = wait(
        client,
        start(
            client,
            p,
            "SELECT id,name FROM records WHERE id<3",
            execution_context={**ctx, "source": "cli"},
        ),
    )
    assert sql["state"] == "completed"
    statements = len(mysql.statements)
    response = client.get("/api/executions", params={"task_id": ctx["task_id"]})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 2
    assert {x["kind"] for x in result["items"]} == {"api", "sql"}
    assert {x["source"] for x in result["items"]} == {"skill", "cli"}
    assert all(
        x["agent_name"] == "Codex" and x["task_id"] == ctx["task_id"]
        for x in result["items"]
    )
    assert not any(
        k in item
        for item in result["items"]
        for k in [
            "sql",
            "body",
            "request",
            "rows",
            "response_preview",
            "headers",
            "host",
            "url",
        ]
    )
    assert (
        "execution_context" not in api["response_preview"]
    )  # metadata was not forwarded upstream
    assert client.get("/api/runs").json()["total"] == 1
    assert len(client.get("/api/sql/jobs").json()) == 1
    assert client.get("/api/runs/" + api["id"]).json()["execution_context"] == ctx
    assert (
        client.get("/api/sql/jobs/" + sql["id"]).json()["execution_context"]["source"]
        == "cli"
    )
    assert (
        client.get("/api/executions", params={"source": "cli", "kind": "api"}).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.get(
            "/api/executions",
            params={
                "kind": "sql",
                "agent_name": "Codex",
                "state": "completed",
                "environment": "测试环境",
            },
        ).json()["total"]
        == 1
    )
    assert (
        client.get("/api/executions", params={"task_id": "x' OR 1=1--"}).json()["total"]
        == 0
    )
    assert (
        client.get("/api/executions", params={"limit": 1, "page": 2}).json()["total"]
        == 2
    )
    assert (
        len(
            client.get("/api/executions", params={"limit": 1, "page": 2}).json()[
                "items"
            ]
        )
        == 1
    )
    assert client.get("/api/executions", params={"search": "%"}).json()["total"] == 0
    assert (
        len(mysql.statements) == statements
    )  # reading history does not execute SQL or open a pool
    # Metadata must not bypass the existing write policy.
    assert (
        client.post(
            "/api/sql/jobs",
            json={
                "connection_id": p["id"],
                "database": "lab",
                "sql": "DELETE FROM records WHERE id=1",
                "execution_context": ctx,
            },
        ).status_code
        == 400
    )
    assert not any(s.upper().startswith("DELETE ") for s in mysql.statements)


def test_projection_is_idempotent_and_retains_http_failure(client, upstream):
    env = environment(client, upstream)
    result = finish(
        client, submit(client, env, path="/error", name="literal_100%")["id"]
    )
    item = client.get("/api/executions").json()["items"][0]
    assert item["source"] == "api" and item["status_code"] == 400
    assert (
        item["state"] == result["state"]
    )  # HTTP failure must not become a fabricated success
    client.app.state.store.index(result)
    assert client.get("/api/executions").json()["total"] == 1
    assert (
        client.get("/api/executions", params={"search": "_100%"}).json()["total"] == 1
    )
    assert (
        client.get("/api/executions", params={"search": "missing_100%"}).json()["total"]
        == 0
    )


def test_known_credentials_are_redacted_from_labels(client, upstream, mysql):
    secret = "fixture-execution-secret-value"
    cred = client.post(
        "/api/credentials", json={"name": "isolated fixture", "value": secret}
    ).json()
    env = environment(client, upstream, credential_ref=cred["id"])
    result = finish(
        client,
        submit(
            client,
            env,
            execution_context={
                "source": "skill",
                "agent_name": secret,
                "task_id": secret,
            },
        )["id"],
    )
    assert result["execution_context"]["agent_name"] == "[REDACTED]"
    p = profile(client, mysql)
    sql = wait(
        client,
        start(
            client,
            p,
            "SELECT id FROM records",
            execution_context={
                "source": "api",
                "agent_name": "fixture-password",
                "task_id": "redaction-task",
            },
        ),
    )
    assert sql["execution_context"]["agent_name"] == "[REDACTED]"
    raw = client.get("/api/executions").text
    assert secret not in raw and "fixture-password" not in raw
    assert "[REDACTED]" in raw


def test_context_validation_auth_and_pagination(client, upstream, mysql):
    env = environment(client, upstream)
    p = profile(client, mysql)
    assert (
        client.get("/api/executions", headers={"Authorization": ""}).status_code == 401
    )
    assert (
        client.get(
            "/api/executions", headers={"Origin": "https://example.invalid"}
        ).status_code
        == 403
    )
    for params in [
        {"limit": 101},
        {"page": 0},
        {"kind": "admin"},
        {"source": "trusted"},
        {"agent_name": "x" * 81},
        {"task_id": "x" * 129},
    ]:
        assert client.get("/api/executions", params=params).status_code == 422
    for context in [
        {"source": "trusted"},
        {"task_id": "bad\r\nlabel"},
        {"agent_name": "x" * 81},
        {"task_id": "x" * 129},
        {"source": "cli", "role": "admin"},
    ]:
        assert (
            client.post(
                "/api/runs",
                json={
                    "environment_id": env["id"],
                    "submission_key": str(uuid4()),
                    "execution_context": context,
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/sql/jobs",
                json={
                    "connection_id": p["id"],
                    "database": "lab",
                    "sql": "SELECT 1",
                    "execution_context": context,
                },
            ).status_code
            == 422
        )
    assert client.get("/api/executions").json()["total"] == 0


def test_legacy_backfill_without_decrypting_sql_and_restart_state(tmp_path):
    root = tmp_path / "legacy"
    s = Store(root)
    record, _ = s.create_run(
        {
            "name": "legacy API",
            "environment_name": "legacy env",
            "method": "GET",
            "environment_id": str(uuid4()),
            "url": "https://example.invalid",
            "request": {},
            "response_preview": "private-response-sentinel",
        },
        str(uuid4()),
        "legacy-digest",
    )
    sid = str(uuid4())
    summary = {
        "id": sid,
        "created_at": now(),
        "connection_name": "legacy SQL",
        "environment": "legacy env",
        "database": "lab",
        "state": "running",
        "mode": "create",
    }
    s.db.execute(
        "CREATE TABLE sql_runs (id TEXT PRIMARY KEY,created TEXT,summary TEXT)"
    )
    s.db.execute(
        "INSERT INTO sql_runs VALUES (?,?,?)",
        (sid, summary["created_at"], json.dumps(summary)),
    )
    s.db.execute("DROP TABLE execution_records")
    s.db.commit()
    s.close()
    # Rebuild must never need this encrypted SQL record or a database connection.
    path = root / "sql/runs" / sid / "record.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-a-decryptable-record", encoding="utf-8")
    app = create_app(root, vault=MemoryVault())
    with TestClient(
        app,
        base_url="http://127.0.0.1:18765",
        headers={"Authorization": "Bearer " + app.state.auth.master},
    ) as c:
        result = c.get("/api/executions").json()
        assert result["total"] == 2
        assert all(
            x["source"] == "unknown" and not x["agent_name"] and not x["task_id"]
            for x in result["items"]
        )
        assert {x["id"]: x["state"] for x in result["items"]} == {
            record["id"]: "interrupted",
            sid: "unknown",
        }
        assert "private-response-sentinel" not in json.dumps(result)
        assert app.state.sql_workspace.pool_status() == []


def test_sql_pending_confirmation_keeps_context(client, mysql):
    p = profile(client, mysql, True)
    ctx = {"source": "skill", "agent_name": "Codex", "task_id": "write-task"}
    j = wait(
        client,
        start(
            client,
            p,
            "UPDATE records SET name='fixture-name' WHERE id=1",
            execution_context=ctx,
        ),
    )
    assert j["state"] == "awaiting_confirmation"
    assert not any(s.upper().startswith("UPDATE ") for s in mysql.statements)
    entry = client.get("/api/executions", params={"task_id": "write-task"}).json()[
        "items"
    ][0]
    assert entry["state"] == "awaiting_confirmation"
    client.post("/api/sql/jobs/" + j["id"] + "/cancel")
    record = client.get("/api/sql/jobs/" + j["id"]).json()
    assert record["execution_context"] == ctx
    items = client.get("/api/executions", params={"task_id": "write-task"}).json()
    assert items["total"] == 1 and items["items"][0]["state"] == "cancelled"


def test_label_redaction_failure_releases_reserved_pool_entry(
    client, mysql, monkeypatch
):
    p = profile(client, mysql)

    def unavailable(key):
        raise RuntimeError("isolated fixture vault unavailable")

    monkeypatch.setattr(client.app.state.sql_workspace.vault, "get", unavailable)
    with pytest.raises(RuntimeError):
        client.post(
            "/api/sql/jobs",
            json={"connection_id": p["id"], "database": "lab", "sql": "SELECT 1"},
        )
    assert client.app.state.sql_workspace.pool_status() == []
    assert client.app.state.sql_workspace.jobs == {}

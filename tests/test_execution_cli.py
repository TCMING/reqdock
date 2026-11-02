import json
import sys
from uuid import uuid4
import httpx
import pytest
from api_post import skill_cli


@pytest.fixture
def cli(monkeypatch, capsys):
    calls = []
    cid = str(uuid4())
    eid = str(uuid4())
    rid = str(uuid4())

    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body, dict(request.url.params)))
        path = request.url.path
        if path == "/api/environment-groups":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "isolated",
                        "members": [{"variable": "demo", "environment": {"id": eid}}],
                    }
                ],
            )
        if path.startswith("/api/cases/"):
            return httpx.Response(
                200,
                json={
                    "id": cid,
                    "request": {
                        "name": "fixture",
                        "body": "{}",
                        "path": "/echo",
                        "execution_context": {
                            "source": "ui",
                            "agent_name": "old",
                            "task_id": "old",
                        },
                    },
                },
            )
        if path == "/api/executions":
            return httpx.Response(200, json={"items": [], "total": 0})
        return httpx.Response(
            200,
            json={
                "id": rid,
                "state": "awaiting_confirmation" if "/sql/" in path else "completed",
            },
        )

    original = httpx.Client
    monkeypatch.setattr(skill_cli, "call_local", lambda *a, **k: None)

    class Vault:
        def __init__(self, *a):
            pass

        def get(self, *a):
            return "fixture-local-master"

    monkeypatch.setattr(skill_cli, "Vault", Vault)
    monkeypatch.setattr(
        skill_cli.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["reqdock", *args])
        skill_cli.main()
        return json.loads(capsys.readouterr().out)

    return run, calls, cid, eid


def test_cli_marks_fresh_source_and_task_instead_of_saved_context(cli):
    run, calls, cid, _ = cli
    run(
        "run",
        "--case",
        cid,
        "--environment",
        "isolated",
        "--platform",
        "demo",
        "--source",
        "skill",
        "--agent",
        "Codex",
        "--task-id",
        "new-task",
    )
    body = next(x[2] for x in calls if x[0] == "POST")
    assert body["execution_context"] == {
        "source": "skill",
        "agent_name": "Codex",
        "task_id": "new-task",
    }


def test_sql_cli_preserves_confirmation_boundary(cli, tmp_path):
    run, calls, cid, _ = cli
    path = tmp_path / "fixture.sql"
    path.write_text("UPDATE records SET name='fixture' WHERE id=1", encoding="utf-8")
    result = run(
        "sql-run",
        "--connection",
        cid,
        "--database",
        "lab",
        "--sql-file",
        str(path),
        "--agent",
        "Codex",
        "--task-id",
        "sql-task",
        "--wait",
        "1",
    )
    assert result["state"] == "awaiting_confirmation"
    posts = [x for x in calls if x[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "/api/sql/jobs"
    assert posts[0][2]["execution_context"] == {
        "source": "cli",
        "agent_name": "Codex",
        "task_id": "sql-task",
    }


def test_records_cli_passes_filters(cli):
    run, calls, _, _ = cli
    run(
        "records",
        "--kind",
        "sql",
        "--source",
        "skill",
        "--agent",
        "Codex",
        "--task-id",
        "shared",
        "--page",
        "2",
    )
    query = calls[-1][3]
    assert (
        query["kind"] == "sql"
        and query["task_id"] == "shared"
        and query["agent_name"] == "Codex"
        and query["page"] == "2"
    )


def test_cli_rejects_invalid_labels_without_echoing_input(cli):
    run, _, cid, _ = cli
    with pytest.raises(ValueError) as error:
        run(
            "run",
            "--case",
            cid,
            "--environment",
            "isolated",
            "--platform",
            "demo",
            "--agent",
            "private-label" * 10,
        )
    assert "private-label" not in str(error.value)

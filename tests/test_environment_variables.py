import json
from uuid import uuid4
from test_workbench import (
    client as client,
    upstream as upstream,
    environment,
    submit,
    finish,
)


def test_selected_environment_variables_resolve_path_header_and_body(client, upstream):
    a = environment(
        client,
        upstream,
        name="environment A",
        variables={"endpoint": upstream, "label": "A", "model": "model-A"},
    )
    b = environment(
        client,
        upstream,
        name="environment B",
        variables={"endpoint": upstream, "label": "B", "model": "model-B"},
    )
    for env, label in [(a, "A"), (b, "B")]:
        run = finish(
            client,
            submit(
                client,
                env,
                path="{{endpoint}}/echo?label={{label}}",
                headers={"X-Label": "{{label}}"},
                body='{"model":"{{model}}","label":"{{label}}"}',
            )["id"],
        )
        assert run["state"] == "completed", run
        wire = json.loads(run["response_preview"])
        assert wire["path"] == "/echo?label=" + label
        assert wire["headers"]["X-Label"] == label
        assert wire["body"] == {"model": "model-" + label, "label": label}
        assert run["environment"]["variables"]["label"] == label
    updated = client.put(
        "/api/environments/" + a["id"],
        json={
            **{k: a[k] for k in ["name", "base_url", "revision"]},
            "variables": {"endpoint": upstream, "label": "changed", "model": "new"},
        },
    ).json()
    assert updated["revision"] == a["revision"] + 1
    assert client.app.state.store.env(a["id"])["variables"]["label"] == "changed"
    assert (
        client.put(
            "/api/environments/" + a["id"],
            json={
                "name": a["name"],
                "base_url": upstream,
                "revision": a["revision"],
                "variables": {},
            },
        ).status_code
        == 400
    )


def test_secret_variables_are_references_resolve_and_redact(client, upstream):
    secret = "fixture-private-variable-value"
    env = environment(
        client,
        upstream,
        variables={
            "copy_first": secret,
            "private": secret,
            "api_key": "fixture-api-key-value",
        },
        secret_variables=["private"],
    )
    assert all("{{secret:" in v for v in env["variables"].values())
    model = {
        "environment_id": env["id"],
        "submission_key": str(uuid4()),
        "name": "secret test",
        "entry_type": "raw",
        "method": "POST",
        "path": "/echo",
        "headers": {"Authorization": "Bearer {{api_key}}"},
        "body": '{"custom":"{{private}}"}',
        "body_type": "json",
        "stream": False,
        "contains": "",
        "expected_status": 200,
        "resource_bindings": [],
    }
    known = []
    url, headers, body = client.app.state.engine.prepare(model, env, known)
    assert headers["Authorization"] == "Bearer fixture-api-key-value"
    assert json.loads(body)["custom"] == secret and secret in known
    run = finish(
        client,
        submit(
            client,
            env,
            headers={"Authorization": "Bearer {{api_key}}"},
            body='{"custom":"{{private}}"}',
        )["id"],
    )
    assert run["state"] == "completed", run
    raw = "".join(
        p.read_text(encoding="utf-8")
        for p in client.app.state.store.root.rglob("*.json")
    )
    assert secret not in raw and "fixture-api-key-value" not in raw
    assert secret not in json.dumps(run)
    assert client.get("/api/environments").json()[0]["variables"] == env["variables"]


def test_variable_validation_missing_nested_and_target_enforcement(client, upstream):
    assert (
        client.post(
            "/api/environments",
            json={"name": "bad", "base_url": upstream, "variables": {"bad name": "x"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/environments",
            json={"name": "bad", "base_url": upstream, "variables": {"a": "x" * 8193}},
        ).status_code
        == 422
    )
    env = environment(
        client,
        upstream,
        variables={"nested": "{{other}}", "outside": "https://example.com"},
    )
    base = {
        "environment_id": env["id"],
        "submission_key": str(uuid4()),
        "path": "/echo",
        "body": '{"x":"{{missing}}"}',
    }
    assert client.post("/api/runs", json=base).status_code == 400
    assert (
        client.post(
            "/api/runs", json={**base, "body": '{"x":"{{nested}}"}'}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/runs", json={**base, "path": "{{outside}}/echo", "body": "{}"}
        ).status_code
        == 400
    )
    assert client.get("/api/runs").json()["total"] == 0


def test_imported_connection_alias_and_omitted_variables_are_preserved(
    client, upstream
):
    env = environment(
        client, upstream, variables={"endpoint": upstream, "label": "original"}
    )
    stored = client.app.state.store.save_env(
        {
            **env,
            "source": {
                "key": "fixture/server/0/endpoint",
                "digest": "fixture",
                "import_revision": env["revision"] + 1,
            },
        },
        env["id"],
        env["revision"],
    )
    data = {"name": "renamed", "base_url": upstream, "revision": stored["revision"]}
    saved = client.put("/api/environments/" + env["id"], json=data).json()
    assert saved["variables"] == env["variables"]
    mismatch = client.put(
        "/api/environments/" + env["id"],
        json={
            **data,
            "revision": saved["revision"],
            "variables": {"endpoint": "http://127.0.0.1:9"},
        },
    )
    assert mismatch.status_code == 400
    assert client.app.state.store.env(env["id"])["variables"] == env["variables"]
